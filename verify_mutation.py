#!/usr/bin/env python3
"""Validate every mutant with an external executor and JSON test events."""
import json
from pathlib import Path
import shutil
import tempfile

from checks import ROOT, capture, environment, save_report, tool_metadata

EXPECTED_EXPRESSIONS = {"size < 10", "size <= 9", "size <= 11"}
WEAK_TEST = 'package policy\nimport "testing"\nfunc TestAllowed(t *testing.T) { if !Allowed(9) { t.Fatal("9 must be allowed") } }\n'


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def main():
    results, metadata, error = [], {}, None
    try:
        metadata = tool_metadata(ROOT, mutation=True)
        # Fixed temp root avoids spaces: go-mutesting splits --exec on plain spaces.
        with tempfile.TemporaryDirectory(prefix="guardrails-mutation-", dir="/tmp") as folder:
            work = Path(folder)
            (work / "go.mod").write_text("module example.com/mutationdemo\n\ngo 1.27.0\n")
            for name in ["mutation_executor.py", "checks.py", "tool-versions.json"]:
                shutil.copyfile(ROOT / name, work / name)
            original = (ROOT / "internal/policy/limit.go").read_bytes()
            source = work / "limit.go"
            source.write_bytes(original)
            for name, tests in {"weak": WEAK_TEST, "boundary": (ROOT / "internal/policy/limit_test.go").read_text()}.items():
                (work / "limit_test.go").write_text(tests)
                baseline = capture(["go", "test", "-count=1", "-timeout=10s", "."], work)
                if baseline["returncode"] != 0:
                    raise RuntimeError("baseline tests failed: " + baseline["output"])
                log = work / f"{name}.jsonl"
                env = environment()
                env["GUARDRAILS_MUTATION_LOG"] = str(log)
                result = capture(["go-mutesting", "--match=^Allowed$", "--exec-timeout=10",
                                  "--exec=python3 mutation_executor.py", "."], work, env=env)
                rows = read_rows(log)
                expected_sources = {"package policy func Allowed(size int) bool { return " + expression + " }" for expression in EXPECTED_EXPRESSIONS}
                wanted = "survived" if name == "weak" else "killed"
                verified = (result["returncode"] == 0 and not result.get("execution_error")
                            and source.read_bytes() == original and len(rows) == 3
                            and {row["source"] for row in rows} == expected_sources
                            and all(row["status"] == wanted for row in rows))
                results.append({"name": name, "verified": verified, "mutants": rows, **result})
                print(f"{'PASS' if verified else 'FAIL'} {name}: {[row['status'] for row in rows]}", flush=True)
                if not verified:
                    raise RuntimeError("individual mutant evidence does not match the experiment")
            # Deliberate failures must never count as a killed mutant.
            controls = {
                "compile failure": (original.decode() + '\nvar invalid int = "wrong"\n', WEAK_TEST),
                "timeout": ('package policy\nfunc Allowed(size int) bool { select{} }\n', WEAK_TEST),
                "unrelated assertion": (original.decode(), 'package policy\nimport "testing"\nfunc TestAllowed(t *testing.T) { t.Fatal("unrelated") }\n'),
                "missing target test": (original.decode(), 'package policy\nimport "testing"\nfunc TestDifferent(t *testing.T) {}\n'),
            }
            for index, (name, (changed_source, tests)) in enumerate(controls.items()):
                changed = work / "mutant.txt"
                changed.write_text(changed_source)
                (work / "limit_test.go").write_text(tests)
                log = work / f"control-{index}.jsonl"
                env = environment()
                env.update(MUTATE_ORIGINAL=str(source), MUTATE_CHANGED=str(changed),
                           MUTATE_TIMEOUT="1", GUARDRAILS_MUTATION_LOG=str(log))
                result = capture(["python3", "mutation_executor.py"], work, env=env, timeout=15)
                rows = read_rows(log)
                verified = (result["returncode"] == 2 and len(rows) == 1 and rows[0]["status"] == "invalid"
                            and source.read_bytes() == original)
                results.append({"name": name, "verified": verified, "mutants": rows, **result})
                print(f"{'PASS' if verified else 'FAIL'} rejects {name}", flush=True)
                if not verified:
                    raise RuntimeError("executor accepted invalid evidence: " + name)
    except Exception as exc:
        error = exc
    target = save_report("mutation-verification", metadata, results, error)
    print("Report:", target)
    if error:
        raise SystemExit(str(error))


if __name__ == "__main__":
    main()
