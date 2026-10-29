import os
import sys
import tempfile
import unittest

from mcpcheck.checks import ERROR, FAIL, INFO, PASS, SKIP, WARNING, run_checks
from mcpcheck.demo import write_demo


class CompliantServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        paths = write_demo(cls.directory.name)
        cls.command = '"%s" "%s"' % (sys.executable, paths["compliant"])
        cls.results = run_checks(command=cls.command, timeout=10)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_every_check_runs(self):
        self.assertGreaterEqual(len(self.results), 18)

    def test_no_failures(self):
        failures = [(check.id, check.detail) for check in self.results if check.status == FAIL]
        self.assertEqual(failures, [])

    def test_expected_checks_are_present(self):
        ids = {check.id for check in self.results}
        for expected in (
            "initialize/server-info",
            "jsonrpc/id-echo",
            "errors/unknown-method",
            "tools/list/shape",
            "tools/call/missing-required",
        ):
            self.assertIn(expected, ids)

    def test_severities_are_valid(self):
        for check in self.results:
            self.assertIn(check.severity, (ERROR, WARNING, INFO))

    def test_details_are_populated(self):
        for check in self.results:
            if check.status == PASS:
                self.assertTrue(check.detail, check.id)


class SloppyServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        paths = write_demo(cls.directory.name)
        cls.command = '"%s" "%s"' % (sys.executable, paths["sloppy"])
        cls.results = run_checks(command=cls.command, timeout=10)
        cls.by_id = {check.id: check for check in cls.results}

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_bad_protocol_version(self):
        self.assertEqual(self.by_id["initialize/protocol-version"].status, FAIL)

    def test_empty_capabilities(self):
        self.assertEqual(self.by_id["initialize/capabilities"].status, FAIL)

    def test_capability_honesty(self):
        self.assertEqual(self.by_id["initialize/tools-honesty"].status, FAIL)

    def test_notification_reply(self):
        self.assertEqual(self.by_id["jsonrpc/notification-silence"].status, FAIL)

    def test_unknown_method(self):
        self.assertEqual(self.by_id["errors/unknown-method"].status, FAIL)

    def test_duplicate_tool_names(self):
        self.assertEqual(self.by_id["tools/list/unique-names"].status, FAIL)

    def test_schema_type(self):
        self.assertEqual(self.by_id["tools/list/schema-type"].status, FAIL)

    def test_required_arguments_ignored(self):
        self.assertEqual(self.by_id["tools/call/missing-required"].status, FAIL)

    def test_tools_still_checked_without_capability(self):
        # The capability is missing, but tools/list works, so the checks run.
        self.assertNotEqual(self.by_id["tools/list/shape"].status, SKIP)


class OnlyFilterTest(unittest.TestCase):
    def test_only_runs_named_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_demo(directory)
            command = '"%s" "%s"' % (sys.executable, paths["compliant"])
            results = run_checks(command=command, timeout=10, only=["jsonrpc/version"])
        self.assertEqual([check.id for check in results], ["jsonrpc/version"])


class HandshakeFailureTest(unittest.TestCase):
    def test_unreachable_server_reports_one_failure(self):
        results = run_checks(command="definitely-not-a-real-command-xyz", timeout=2)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, FAIL)
        self.assertIn("could not start", results[0].detail)

    def test_server_that_exits_immediately(self):
        command = '"%s" -c "import sys; sys.exit(1)"' % sys.executable
        results = run_checks(command=command, timeout=2)
        self.assertEqual(results[0].status, FAIL)


if __name__ == "__main__":
    unittest.main()
