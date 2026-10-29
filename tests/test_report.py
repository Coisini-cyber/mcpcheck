import json
import unittest

from mcpcheck.checks import ERROR, FAIL, INFO, PASS, SKIP, WARNING, CheckResult
from mcpcheck.fuzz import BAD, OK, WEAK, FuzzResult
from mcpcheck.report import Report, render_console, render_json, render_markdown


def sample_report(ok=True):
    checks = [
        CheckResult("initialize/server-info", "serverInfo", ERROR, PASS, "demo 1.0"),
        CheckResult("tools/list/shape", "tools", ERROR, PASS if ok else FAIL, "2 tools"),
        CheckResult("tools/list/pagination", "pagination", WARNING, PASS, "single page"),
        CheckResult("resources/list", "resources", WARNING, SKIP, "not supported"),
        CheckResult("performance/ping", "ping", INFO, PASS, "0 ms"),
    ]
    fuzz = [
        FuzzResult("invalid-json-brace", "a single brace", OK, ""),
        FuzzResult("deep-nesting", "200 levels", BAD if not ok else OK, "server exited"),
        FuzzResult("params-string", "params is a string", WEAK, "accepted"),
    ]
    return Report(target="python server.py", checks=checks, fuzz=fuzz)


class SummaryTest(unittest.TestCase):
    def test_counts(self):
        report = sample_report(ok=False)
        self.assertEqual(report.errors_failed, 1)
        self.assertEqual(report.warnings_failed, 0)
        self.assertEqual(report.checks_passed, 3)
        self.assertEqual(report.checks_skipped, 1)
        self.assertEqual(report.fuzz_bad, 1)
        self.assertEqual(report.fuzz_weak, 1)
        self.assertFalse(report.ok)

    def test_ok(self):
        self.assertTrue(sample_report(ok=True).ok)


class MarkdownTest(unittest.TestCase):
    def test_sections(self):
        text = render_markdown(sample_report(ok=False))
        self.assertIn("# MCP conformance report", text)
        self.assertIn("**Result:** FAIL", text)
        self.assertIn("## Checks", text)
        self.assertIn("## Failures", text)
        self.assertIn("## Fuzz", text)

    def test_rows(self):
        text = render_markdown(sample_report(ok=True))
        self.assertIn("| `initialize/server-info` | error | pass |", text)
        self.assertIn("| `resources/list` | warning | skip |", text)
        self.assertIn("| `deep-nesting` | ok |", text)

    def test_failure_detail_is_included(self):
        text = render_markdown(sample_report(ok=False))
        self.assertIn("### `tools/list/shape`", text)


class JsonTest(unittest.TestCase):
    def test_round_trip(self):
        data = json.loads(render_json(sample_report(ok=False)))
        self.assertFalse(data["ok"])
        self.assertEqual(data["summary"]["errors_failed"], 1)
        self.assertEqual(len(data["checks"]), 5)
        self.assertEqual(len(data["fuzz"]), 3)


class ConsoleTest(unittest.TestCase):
    def test_lines(self):
        text = render_console(sample_report(ok=False))
        self.assertIn("pass error", text)
        self.assertIn("FAIL error", text)
        self.assertIn("BAD", text)
        self.assertIn("FAIL:", text)

    def test_pass_verdict(self):
        self.assertIn("PASS:", render_console(sample_report(ok=True)))


if __name__ == "__main__":
    unittest.main()
