import sys
import tempfile
import unittest

from mcpcheck.demo import write_demo
from mcpcheck.fuzz import BAD, OK, WEAK, build_cases, run_fuzz


class CaseTest(unittest.TestCase):
    def test_cases_are_unique_and_described(self):
        cases = build_cases()
        ids = [case.id for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        for case in cases:
            self.assertTrue(case.description)
            self.assertIsInstance(case.payload, str)

    def test_expected_coverage(self):
        ids = {case.id for case in build_cases()}
        for expected in (
            "invalid-json-brace",
            "missing-jsonrpc",
            "unknown-method",
            "deep-nesting",
            "huge-params",
            "tools-call-empty-params",
            "notification-unknown",
        ):
            self.assertIn(expected, ids)


class CompliantFuzzTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        paths = write_demo(cls.directory.name)
        cls.command = '"%s" "%s"' % (sys.executable, paths["compliant"])
        cls.results = run_fuzz(command=cls.command, timeout=10, case_timeout=0.5)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_every_case_ran(self):
        self.assertEqual(len(self.results), len(build_cases()))

    def test_no_crashes(self):
        bad = [result.id for result in self.results if result.outcome == BAD]
        self.assertEqual(bad, [])

    def test_no_weak_responses(self):
        weak = [(result.id, result.detail) for result in self.results if result.outcome == WEAK]
        self.assertEqual(weak, [])

    def test_outcomes_are_known(self):
        for result in self.results:
            self.assertIn(result.outcome, (OK, WEAK, BAD))


class SloppyFuzzTest(unittest.TestCase):
    def test_crash_on_malformed_json_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_demo(directory)
            command = '"%s" "%s"' % (sys.executable, paths["sloppy"])
            results = run_fuzz(command=command, timeout=10, case_timeout=0.5)
        self.assertTrue(results)
        self.assertEqual(results[0].id, "invalid-json-brace")
        self.assertEqual(results[0].outcome, BAD)

    def test_stops_after_a_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_demo(directory)
            command = '"%s" "%s"' % (sys.executable, paths["sloppy"])
            results = run_fuzz(command=command, timeout=10, case_timeout=0.5)
        self.assertEqual(len(results), 1)


class FilterTest(unittest.TestCase):
    def test_only_runs_named_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_demo(directory)
            command = '"%s" "%s"' % (sys.executable, paths["compliant"])
            results = run_fuzz(
                command=command, timeout=10, case_timeout=0.5, only=["unknown-method"]
            )
        self.assertEqual([result.id for result in results], ["unknown-method"])


class UnreachableTest(unittest.TestCase):
    def test_handshake_failure(self):
        results = run_fuzz(command="definitely-not-a-real-command-xyz", timeout=2)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, BAD)


if __name__ == "__main__":
    unittest.main()
