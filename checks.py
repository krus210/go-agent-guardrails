"""Evidence checks shared by the examples and the mutation executor."""
import datetime
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
VERSIONS = json.loads((ROOT / "tool-versions.json").read_text())
# These outcomes never establish the behaviour of the intended test.
INFRA_FAILURE = re.compile(
    r"\[(?:build|setup) failed\]|\(typecheck\)|panic: test timed out|"
    r"level=error|unknown linters|no Go files|no go files|no space left on device|"
    r"failed to load|failed to execute|permission denied|operation not permitted",
    re.IGNORECASE,
)
BUBBLE_DEADLOCK = r"panic: deadlock: all goroutines in bubble are blocked"


def environment():
    env = os.environ.copy()
    env.setdefault("GOTOOLCHAIN", VERSIONS["go"])
    return env


def capture(command, cwd, timeout=120, env=None):
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env or environment(), text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    except OSError as exc:
        return {"returncode": None, "output": str(exc), "execution_error": True}
    try:
        output, _ = process.communicate(timeout=timeout)
        return {"returncode": process.returncode, "output": output, "execution_error": False}
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # The process may finish between the timeout and termination.
        output, _ = process.communicate()
        return {"returncode": process.returncode, "output": output, "execution_error": True,
                "reason": "process deadline exceeded"}


def go_output(result):
    events = []
    for line in result["output"].splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return [], result["output"], "non-JSON go test output"
        if not isinstance(event, dict):
            return [], result["output"], "invalid go test event"
        events.append(event)
    output = "".join(event.get("Output", "") for event in events)
    return events, output, None


def judge_go(result, package, test=None, diagnostic=None, failure=False):
    events, output, malformed = go_output(result)
    if result.get("execution_error") or malformed or INFRA_FAILURE.search(output):
        return False, malformed or result.get("reason", "build or execution failure")
    expected_code = 1 if failure else 0
    if result["returncode"] != expected_code:
        return False, f"expected exit {expected_code}, got {result['returncode']}"
    package_events = [event for event in events if event.get("Package") == package]
    if not package_events:
        return False, "target package did not run"
    if test and not any(e.get("Action") == "run" and e.get("Test") == test for e in package_events):
        return False, "target test did not run"
    if failure:
        test_output = "".join(e.get("Output", "") for e in package_events
                              if e.get("Test") == test or e.get("Test", "").startswith(test + "/"))
        if not diagnostic or not re.search(diagnostic, test_output):
            return False, "expected test diagnostic absent"
        # A synctest panic may terminate the binary before a per-test fail event.
        if diagnostic != BUBBLE_DEADLOCK and not any(
            e.get("Action") == "fail" and (e.get("Test") == test or e.get("Test", "").startswith(test + "/"))
            for e in package_events
        ):
            return False, "target test failure event absent"
    elif not any(e.get("Action") == "pass" and not e.get("Test") for e in package_events):
        return False, "target package pass event absent"
    if not failure and test and not any(e.get("Action") == "pass" and e.get("Test") == test for e in package_events):
        return False, "target test did not pass"
    return True, "expected test evidence observed"


def judge_lint(result, linter=None, diagnostic=None):
    if result.get("execution_error") or INFRA_FAILURE.search(result["output"]):
        return False, "linter configuration, typecheck or execution failure"
    if result["returncode"] != (1 if linter else 0):
        return False, "unexpected linter exit code"
    try:
        report = json.loads(result["output"])
    except json.JSONDecodeError:
        return False, "invalid linter JSON report"
    if not isinstance(report, dict) or "Issues" not in report:
        return False, "linter report has no Issues field"
    issues = report["Issues"] or []
    if not isinstance(issues, list) or any(i.get("FromLinter") == "typecheck" for i in issues):
        return False, "typecheck or malformed issues"
    if not linter:
        return not issues, "no issues" if not issues else "unexpected issues"
    found = [issue for issue in issues if issue.get("FromLinter") == linter
             and issue.get("Pos", {}).get("Line", 0) > 0
             and issue.get("Pos", {}).get("Filename", "").endswith(".go")
             and re.search(diagnostic, issue.get("Text", ""))]
    return bool(found), "typed linter diagnostic found" if found else "expected linter issue absent"


def judge_vet(result):
    if result.get("execution_error") or INFRA_FAILURE.search(result["output"]):
        return False, "vet could not analyse the package"
    found = re.search(r"(?m)^.*context\.go:\d+:\d+: .*cancel function.*", result["output"])
    return result["returncode"] == 1 and bool(found), "lostcancel diagnostic required"


def tool_metadata(cwd, mutation=False):
    info = {}
    go = capture(["go", "env", "GOVERSION"], cwd)
    if go["returncode"] != 0 or go["output"].strip() != VERSIONS["go"]:
        raise RuntimeError(f"Use GOTOOLCHAIN={VERSIONS['go']}; current Go could not match the pin")
    info["go"] = go["output"].strip()
    env_info = capture(["go", "env", "GOTOOLCHAIN", "GOOS", "GOARCH", "CGO_ENABLED"], cwd)
    if env_info["returncode"] != 0:
        raise RuntimeError("could not determine Go execution environment")
    for key, value in zip(["GOTOOLCHAIN", "GOOS", "GOARCH", "CGO_ENABLED"], env_info["output"].splitlines()):
        info[key] = value
    if mutation:
        import shutil
        path = shutil.which("go-mutesting")
        if not path:
            raise RuntimeError("Install the pinned go-mutesting from README.md")
        built = capture(["go", "version", "-m", path], cwd)
        wanted = f"\tmod\t{VERSIONS['mutation-module']}\t{VERSIONS['mutation-version']}\t"
        if built["returncode"] != 0 or wanted not in built["output"]:
            raise RuntimeError("go-mutesting does not match tool-versions.json")
        info["go-mutesting"] = VERSIONS["mutation-version"]
    else:
        lint = capture(["golangci-lint", "version"], cwd)
        match = re.search(r"version (\S+) built with (\S+)", lint["output"])
        if lint["returncode"] != 0 or not match or match.group(1) != VERSIONS["golangci-lint"] or match.group(2) != VERSIONS["go"]:
            raise RuntimeError("golangci-lint version/build toolchain does not match tool-versions.json")
        info["golangci-lint"] = match.group(1)
        info["golangci-lint-build-go"] = match.group(2)
    return info


def normalize(output):
    output = output.replace(str(ROOT), "<example>")
    output = re.sub(r"/[^\s:\"']*/pkg/mod/", "<module-cache>/", output)
    output = re.sub(r"/[^\s:\"']*/(?:libexec/)?src/", "<GOROOT>/src/", output)
    output = re.sub(r"/[^\s:\"']*/guardrails-[^/\s:\"']+/", "<experiment>/", output)
    output = output.replace(str(Path.home()), "<home>")
    return re.sub(r"0x[0-9a-fA-F]+", "<address>", output)


def save_report(name, metadata, results, error=None):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    passed = bool(results) and error is None and all(row["verified"] for row in results)
    data = {"schema_version": 2, "created_at": stamp, "tools": metadata,
            "verified": passed, "error": str(error) if error else None, "results": results}
    text = normalize(json.dumps(data, ensure_ascii=False, indent=2)) + "\n"
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    target = reports / f"{name}-{stamp}.json"
    target.write_text(text)
    # A failed/incomplete run never replaces the last complete successful report.
    if passed:
        with tempfile.NamedTemporaryFile(mode="w", dir=ROOT, delete=False) as stream:
            stream.write(text)
            temporary = stream.name
        os.replace(temporary, ROOT / f"{name}.json")
    return target
