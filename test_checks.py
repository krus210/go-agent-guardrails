"""Regression tests for false positives reported in the technical review."""
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from mutation_executor import classify
from checks import normalize, save_report

from checks import BUBBLE_DEADLOCK, judge_go, judge_lint, judge_vet

PACKAGE = "example.com/guardrails/testdata/deadlock"
TEST = "TestDeadlock"


def go_result(output, code=1, action="fail", started=True):
    events = []
    if started:
        events.append({"Package": PACKAGE, "Test": TEST, "Action": "run"})
    events += [{"Package": PACKAGE, "Test": TEST, "Action": "output", "Output": output},
               {"Package": PACKAGE, "Test": TEST, "Action": action}]
    return {"returncode": code, "output": "\n".join(map(json.dumps, events)), "execution_error": False}


class EvidenceTests(unittest.TestCase):
    def test_real_bubble_panic_is_accepted(self):
        self.assertTrue(judge_go(go_result(BUBBLE_DEADLOCK), PACKAGE, TEST, BUBBLE_DEADLOCK, True)[0])

    def test_deadlock_in_path_is_not_evidence(self):
        for output in ["testdata/deadlock/deadlock_test.go:10:6: expected '('\nFAIL [setup failed]",
                       "FAIL example.com/guardrails/testdata/deadlock [build failed]",
                       "panic: test timed out after 10s"]:
            with self.subTest(output=output):
                self.assertFalse(judge_go(go_result(output), PACKAGE, TEST, BUBBLE_DEADLOCK, True)[0])

    def test_panic_without_target_start_is_not_evidence(self):
        self.assertFalse(judge_go(go_result(BUBBLE_DEADLOCK, started=False), PACKAGE, TEST, BUBBLE_DEADLOCK, True)[0])

    def test_nonzero_tool_codes_are_not_linter_findings(self):
        for code in [2, 3, 5]:
            self.assertFalse(judge_lint({"returncode": code, "output": "bodyclose depguard gocognit"}, "bodyclose", "body")[0])

    def test_typecheck_issue_with_linter_name_in_path_is_rejected(self):
        report = {"Issues": [{"FromLinter": "typecheck", "Text": "bodyclose",
                              "Pos": {"Filename": "testdata/bodyclose/client.go", "Line": 1}}]}
        self.assertFalse(judge_lint({"returncode": 1, "output": json.dumps(report)}, "bodyclose", "bodyclose")[0])

    def test_real_structured_linter_issue_is_accepted(self):
        issue = {"FromLinter": "bodyclose", "Text": "response body must be closed",
                 "Pos": {"Filename": "client.go", "Line": 12}}
        self.assertTrue(judge_lint({"returncode": 1, "output": json.dumps({"Issues": [issue]})}, "bodyclose", "body must be closed")[0])

    def test_empty_report_cannot_claim_a_violation(self):
        self.assertFalse(judge_lint({"returncode": 0, "output": '{"Issues": null}'}, "gocognit", "complexity")[0])

    def test_vet_needs_positioned_diagnostic(self):
        for text in ["no Go files in lostcancel", "cancel function\n[build failed]"]:
            self.assertFalse(judge_vet({"returncode": 1, "output": text})[0])

    def test_build_failure_overrides_matching_assertion(self):
        result = go_result("panic: deadlock: all goroutines in bubble are blocked\n[build failed]")
        self.assertFalse(judge_go(result, PACKAGE, TEST, BUBBLE_DEADLOCK, True)[0])


class MutationAndReportTests(unittest.TestCase):
    def test_report_normalizes_machine_details(self):
        output = (f"{Path.home()}/Library/Caches/go-build/entry: denied\n"
                  "/opt/homebrew/Cellar/go/1.27.1/libexec/src/internal/sync/mutex.go:70 0x123abc\n"
                  "/opt/homebrew/Cellar/go/1.27.1/libexec/src/time/sleep.go:215")
        normalized = normalize(output)
        self.assertNotIn(str(Path.home()), normalized)
        self.assertNotIn("/opt/homebrew/", normalized)
        self.assertNotIn("0x123abc", normalized)
        self.assertIn("<GOROOT>/src/internal/sync/mutex.go:70", normalized)

    def test_mutation_timeout_is_invalid_even_with_test_fail_event(self):
        result = go_result("panic: test timed out after 1s")
        self.assertEqual(classify(result)[0], "invalid")

    def test_mutation_compile_failure_is_not_killed(self):
        result = {"returncode": 1, "output": "FAIL [build failed]"}
        self.assertEqual(classify(result)[0], "invalid")

    def test_skipped_test_is_not_a_surviving_mutant(self):
        events = [{"Package": "example.com/mutationdemo", "Test": "TestAllowed", "Action": "run"},
                  {"Package": "example.com/mutationdemo", "Test": "TestAllowed", "Action": "skip"},
                  {"Package": "example.com/mutationdemo", "Action": "pass"}]
        result = {"returncode": 0, "output": "\n".join(map(json.dumps, events))}
        self.assertEqual(classify(result)[0], "invalid")

    def test_failed_run_preserves_successful_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            existing = root / "verification.json"
            existing.write_text("previous successful report")
            with patch("checks.ROOT", root):
                failure = save_report("verification", {}, [{"verified": False}], "setup failure")
            self.assertTrue(failure.exists())
            self.assertEqual(existing.read_text(), "previous successful report")


if __name__ == "__main__":
    unittest.main()
