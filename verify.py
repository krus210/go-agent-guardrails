#!/usr/bin/env python3
"""Run examples and adversarial controls; validate structured evidence, not keywords."""
from pathlib import Path
import re
import shutil
import tempfile

from checks import (ROOT, BUBBLE_DEADLOCK, capture, environment, judge_go, judge_lint,
                    judge_vet, save_report, tool_metadata)

MODULE = "example.com/guardrails"
RESULTS = []
LINT_FLAGS = ["--output.json.path=stdout", "--output.text.path=/dev/null",
              "--show-stats=false", "--max-issues-per-linter=0", "--max-same-issues=0",
              "--uniq-by-line=false"]
SCHEMA = ROOT / "schemas/golangci.v2.10.jsonschema.json"


def check(name, command, cwd, judge, reject=False, env=None):
    result = capture(command, cwd, env=env)
    accepted, reason = judge(result)
    verified = (not accepted) if reject else accepted
    # Rejection controls must actually execute; a missing binary is not evidence.
    verified = verified and not result.get("execution_error")
    RESULTS.append({"name": name, "command": command, "expected": "reject" if reject else "accept",
                    "verified": verified, "reason": reason, **result})
    print(f"{'PASS' if verified else 'FAIL'} {name}", flush=True)
    if not verified:
        raise RuntimeError(f"{name}: {reason}\n{result['output']}")


def go(name, cwd, target, test=None, diagnostic=None, reject=False, env=None):
    package = MODULE if target == "." else MODULE + "/" + target.removeprefix("./")
    command = ["go", "test", "-json", "-count=1", "-timeout=10s"]
    if test:
        command += ["-run", "^" + re.escape(test) + "$"]
    command += [target]
    check(name, command, cwd, lambda result: judge_go(result, package, test, diagnostic,
          failure=diagnostic is not None), reject, env)


def lint(name, cwd, target, config=".golangci.yml", linter=None, diagnostic=None, reject=False):
    command = ["golangci-lint", "run", "-c", config, *LINT_FLAGS, target]
    check(name, command, cwd, lambda result: judge_lint(result, linter, diagnostic), reject)


def schema(name, cwd, config, invalid_key=None):
    command = ["golangci-lint", "config", "verify", "-c", config, "--schema", SCHEMA.as_uri()]
    def judge(result):
        if result.get("execution_error"):
            return False, "schema validator could not execute"
        if invalid_key:
            return result["returncode"] == 3 and invalid_key in result["output"] and "additional propert" in result["output"], "unknown key must fail schema validation"
        return result["returncode"] == 0, "schema validation must succeed"
    check(name, command, cwd, judge)


def main():
    metadata = {}
    error = None
    try:
        metadata = tool_metadata(ROOT)
        for config in [".golangci.yml", "naming.yml", "complexity.yml", "exhaustruct.yml", "depguard.yml"]:
            schema("schema " + config, ROOT, config)
        check("normal tests and race detector", ["go", "test", "-race", "-json", "-count=1", "-timeout=30s", "./..."],
              ROOT, lambda result: judge_go(result, MODULE))
        lint("base linters", ROOT, "./...")
        lint("revive catches initialism", ROOT, "./testdata/naming", "naming.yml", "revive", r"UserId should be UserID")
        lint("gocognit catches nested conditions", ROOT, "./testdata/complexity", "complexity.yml", "gocognit", r"cognitive complexity 6 .*high")
        # Isolate bodyclose while using the same verified base configuration.
        command = ["golangci-lint", "run", "-c", ".golangci.yml", "--enable-only=bodyclose", *LINT_FLAGS, "./testdata/bodyclose"]
        body_judge = lambda result: judge_lint(result, "bodyclose", r"response body must be closed")
        check("bodyclose finds missing close", command, ROOT, body_judge)
        check("vet finds lost cancel", ["go", "vet", "./testdata/lostcancel"], ROOT, judge_vet)
        go("goleak finds blocked sender", ROOT, "./testdata/leak", "TestLeak", r"unexpected goroutines")
        go("synctest finds channel deadlock", ROOT, "./testdata/deadlock", "TestDeadlock", BUBBLE_DEADLOCK)
        check("race detector finds shared access", ["go", "test", "-race", "-json", "-count=1", "./testdata/race"], ROOT,
              lambda result: judge_go(result, MODULE + "/testdata/race", "TestRace", r"WARNING: DATA RACE", True))
        with tempfile.TemporaryDirectory(prefix="guardrails-review-") as folder:
            work = Path(folder) / "example"
            shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("reports", "*-verification.json", "verification.json", "__pycache__"))
            broken = work / "testdata/deadlock/deadlock_test.go"
            original = broken.read_text()
            broken.write_text(original.replace("func TestDeadlock", "func broken(\nfunc TestDeadlock"))
            go("reject deadlock setup failure", work, "./testdata/deadlock", "TestDeadlock", BUBBLE_DEADLOCK, True)
            broken.write_text('package deadlock\nimport ("testing"; "sync")\nfunc TestDeadlock(t *testing.T) { var mu sync.Mutex; mu.Lock(); mu.Lock() }\n')
            go("reject mutex timeout as bubble deadlock", work, "./testdata/deadlock", "TestDeadlock", BUBBLE_DEADLOCK, True)
            broken.write_text(original)
            type_error = work / "testdata/bodyclose/broken.go"
            type_error.write_text('package bodyclose\nvar wrong int = "bodyclose"\n')
            check("reject bodyclose typecheck failure", command, work, body_judge, True)
            type_error.unlink()
            bad_config = work / "bad.yml"
            bad_config.write_text('version: "2"\nlinters:\n  enable: [bodyclose-missing]\n')
            lint("reject linter configuration failure", work, "./testdata/bodyclose", "bad.yml", "bodyclose", r"response body must be closed", True)
            lint("reject no-Go-files result", work, "./does-not-exist", ".golangci.yml", "bodyclose", r"response body must be closed", True)
            bad_config.write_text((work / "complexity.yml").read_text().replace("min-complexity", "min-complexty"))
            schema("schema rejects misspelled option", work, "bad.yml", "min-complexty")
            policy = work / "internal/policy/limit.go"
            tests = work / "internal/policy/limit_test.go"
            original_policy, original_tests = policy.read_text(), tests.read_text()
            policy.write_text(original_policy.replace("size <= 10", "size < 10"))
            tests.write_text('package policy\nimport "testing"\nfunc TestAllowed(t *testing.T) { if !Allowed(9) { t.Fatal("9 must be allowed") } }\n')
            go("boundary mutation survives weak test", work, "./internal/policy", "TestAllowed")
            tests.write_text(original_tests)
            go("boundary mutation killed by boundary test", work, "./internal/policy", "TestAllowed", r"Allowed\(10\) = false, want true")
            policy.write_text(original_policy)
            report = work / "internal/report/report.go"
            tests = work / "internal/report/report_test.go"
            original_report, original_tests = report.read_text(), tests.read_text()
            forward_stub = original_report.split("func Forward")[0] + 'func Forward(ctx context.Context, out chan<- int, value int) <-chan struct{} { done := make(chan struct{}); close(done); return done }\n'
            variants = [
                ("empty implementation", forward_stub, r"sender completed before cancellation"),
                ("missing cancellation case", original_report.replace("case <-ctx.Done():", ""), BUBBLE_DEADLOCK),
                ("missing done close", original_report.replace("defer close(done)", ""), BUBBLE_DEADLOCK),
                ("cancellation checked only before send", re.sub(r"select \{\s*case out <- value:\s*case <-ctx.Done\(\):\s*\}", "if ctx.Err() != nil { return }; out <- value", original_report), BUBBLE_DEADLOCK),
                ("unconditional send", re.sub(r"select \{\s*case out <- value:\s*case <-ctx.Done\(\):\s*\}", "out <- value", original_report), BUBBLE_DEADLOCK),
            ]
            for name, source, diagnostic in variants:
                if source == original_report:
                    raise RuntimeError("mutation did not change Forward: " + name)
                report.write_text(source)
                go("Forward cancellation rejects " + name, work, "./internal/report", "TestForwardCancellation", diagnostic)
            report.write_text(original_report.replace("out <- value", "out <- value + 1"))
            go("Forward delivery checks received value", work, "./internal/report", "TestForwardDelivery", r"Forward\(\) delivered 43, want 42")
            report.write_text(original_report.replace("Region: record.Region", 'Region: ""'))
            tests.write_text('package report\nimport "testing"\nfunc TestToView(t *testing.T) { if got := ToView(Record{ID: 7, Region: "north"}); got.ID != 7 { t.Fatal(got) } }\n')
            go("wrong field survives partial assertion", work, "./internal/report", "TestToView")
            tests.write_text(original_tests)
            go("whole result detects wrong field", work, "./internal/report", "TestToView", r"ToView\(\) = .*want")
            report.write_text(original_report.replace("type View struct {", "type View struct {\n Currency string"))
            go("new zero field survives equality", work, "./internal/report", "TestToView")
            lint("exhaustruct checks new field", work, "./internal/report", "exhaustruct.yml", "exhaustruct", r"View is missing field Currency")
            bad_config.write_text((work / "exhaustruct.yml").read_text().replace("internal/report", "internal/typo"))
            schema("schema permits syntactically valid wrong scope", work, "bad.yml")
            lint("counterexample rejects wrong exhaustruct scope", work, "./internal/report", "bad.yml", "exhaustruct", r"View is missing field Currency", True)
            report.write_text(original_report)
            calc = work / "internal/reportcalc/calc.go"
            original_calc = calc.read_text()
            for imported, forbidden in [("storage", True), ("storage/reader", True), ("storagecache", False)]:
                calc.write_text(original_calc.replace("package reportcalc", f'package reportcalc\nimport _ "{MODULE}/internal/{imported}"'))
                lint("depguard boundary " + imported, work, "./internal/reportcalc", "depguard.yml", "depguard" if forbidden else None, r"not allowed" if forbidden else None)
                go("architecture boundary " + imported, work, ".", "TestArchitecture", r"ARCH001:" if forbidden else None)
            calc.write_text(original_calc.replace("package reportcalc", f'package reportcalc\nimport _ "{MODULE}/internal/shared"'))
            lint("depguard does not traverse imports", work, "./internal/reportcalc", "depguard.yml")
            go("architecture finds transitive dependency", work, ".", "TestArchitecture", r"ARCH001:.*reportcalc -> .*shared -> .*storage")
            calc.write_text(original_calc)
            nested = work / "internal/reportcalc/nested"
            nested.mkdir()
            (nested / "nested.go").write_text(f'package nested\nimport _ "{MODULE}/internal/storage"\n')
            lint("depguard includes nested calculation packages", work, "./internal/reportcalc/...", "depguard.yml", "depguard", r"not allowed")
            go("architecture includes nested calculation packages", work, ".", "TestArchitecture", r"ARCH001:")
            shutil.rmtree(nested)
            test_import = work / "internal/reportcalc/import_test.go"
            test_import.write_text(f'package reportcalc\nimport _ "{MODULE}/internal/storage"\n')
            lint("depguard excludes test-only dependencies", work, "./internal/reportcalc", "depguard.yml")
            go("architecture excludes test-only dependencies", work, ".", "TestArchitecture")
            test_import.unlink()
            tagged_env = environment()
            tagged_env["GOFLAGS"] = "-tags=guardrails_violation"
            go("architecture uses shared build flags", work, ".", "TestArchitecture", r"ARCH001:", env=tagged_env)
            arch = work / "architecture_test.go"
            original_arch = arch.read_text()
            arch.write_text(original_arch.replace('"/internal/storage"', '"/internal/renamedstorage"'))
            go("architecture rejects missing target", work, ".", "TestArchitecture", r"architecture target package missing")
            arch.write_text(original_arch.replace('"/internal/reportcalc"', '"/internal/missingcalc"'))
            go("architecture rejects empty source scope", work, ".", "TestArchitecture", r"no calculation packages checked")
            arch.write_text(original_arch)
            (work / "internal/reportcalc/broken.go").write_text('package reportcalc\nvar broken int = "wrong"\n')
            go("architecture fails when graph cannot load", work, ".", "TestArchitecture", r"cannot load the production package graph")
    except Exception as exc:
        error = exc
    target = save_report("verification", metadata, RESULTS, error)
    print("Report:", target)
    if error:
        raise SystemExit(str(error))


if __name__ == "__main__":
    main()
