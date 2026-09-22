#!/usr/bin/env python3
"""go-mutesting executor: 0=killed, 1=survived, 2=invalid experiment."""
import hashlib
import json
import os
from pathlib import Path
import re

from checks import capture, environment, go_output, INFRA_FAILURE, judge_go

PACKAGE = "example.com/mutationdemo"
TEST = "TestAllowed"
ASSERTION = r"(?:Allowed\(\d+\) = (?:true|false), want (?:true|false)|9 must be allowed)"


def classify(result):
    _, output, malformed = go_output(result)
    if result.get("execution_error") or malformed or INFRA_FAILURE.search(output) or "panic:" in output:
        return "invalid", "compilation, timeout, panic or execution failure"
    if result["returncode"] == 0:
        accepted, reason = judge_go(result, PACKAGE, TEST)
        return ("survived", reason) if accepted else ("invalid", reason)
    accepted, reason = judge_go(result, PACKAGE, TEST, ASSERTION, failure=True)
    return ("killed", reason) if accepted else ("invalid", reason)


def main():
    original = Path(os.environ["MUTATE_ORIGINAL"])
    changed = Path(os.environ["MUTATE_CHANGED"])
    log = Path(os.environ["GUARDRAILS_MUTATION_LOG"])
    timeout = int(os.environ["MUTATE_TIMEOUT"])
    before = original.read_bytes()
    mutated = changed.read_bytes()
    result = {"returncode": None, "output": "executor failed", "execution_error": True}
    try:
        original.write_bytes(mutated)
        result = capture(["go", "test", "-json", "-count=1", f"-timeout={timeout}s",
                          "-run", "^TestAllowed$", "."], original.parent,
                         timeout=timeout + 5, env=environment())
    finally:
        original.write_bytes(before)
    status, reason = classify(result)
    row = {"source": re.sub(r"\s+", " ", mutated.decode()).strip(),
           "sha256": hashlib.sha256(mutated).hexdigest(), "status": status,
           "reason": reason, **result}
    with log.open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    return {"killed": 0, "survived": 1, "invalid": 2}[status]


if __name__ == "__main__":
    raise SystemExit(main())
