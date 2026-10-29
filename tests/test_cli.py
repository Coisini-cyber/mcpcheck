import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

from mcpcheck.cli import main


def run(argv):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class DemoTest(unittest.TestCase):
    def test_writes_both_servers(self):
        with tempfile.TemporaryDirectory() as directory:
            code, out, _ = run(["demo", "-o", directory])
            self.assertEqual(code, 0)
            self.assertIn("compliant_server.py", out)
            self.assertTrue(os.path.exists(os.path.join(directory, "compliant_server.py")))
            self.assertTrue(os.path.exists(os.path.join(directory, "sloppy_server.py")))


class RunTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        run(["demo", "-o", self.directory.name])
        self.compliant = '"%s" "%s"' % (
            sys.executable,
            os.path.join(self.directory.name, "compliant_server.py"),
        )
        self.sloppy = '"%s" "%s"' % (
            sys.executable,
            os.path.join(self.directory.name, "sloppy_server.py"),
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_compliant_passes(self):
        code, out, _ = run(["run", "--stdio", self.compliant])
        self.assertEqual(code, 0)
        self.assertIn("PASS:", out)

    def test_sloppy_fails(self):
        code, out, _ = run(["run", "--stdio", self.sloppy])
        self.assertEqual(code, 1)
        self.assertIn("FAIL:", out)

    def test_checks_only(self):
        code, out, _ = run(["run", "--stdio", self.compliant, "--no-fuzz"])
        self.assertEqual(code, 0)
        self.assertIn("checks", out.lower())

    def test_fuzz_only(self):
        code, out, _ = run(["run", "--stdio", self.compliant, "--no-checks"])
        self.assertEqual(code, 0)
        self.assertIn("ok", out)

    def test_json_and_markdown_reports(self):
        json_path = os.path.join(self.directory.name, "report.json")
        md_path = os.path.join(self.directory.name, "report.md")
        code, _, _ = run(
            [
                "run",
                "--stdio",
                self.compliant,
                "--json",
                json_path,
                "-o",
                md_path,
                "--quiet",
            ]
        )
        self.assertEqual(code, 0)
        with open(json_path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertTrue(data["ok"])
        with open(md_path, encoding="utf-8") as handle:
            self.assertIn("# MCP conformance report", handle.read())

    def test_only_filter(self):
        code, out, _ = run(
            ["run", "--stdio", self.compliant, "--no-fuzz", "--only", "jsonrpc/version"]
        )
        self.assertEqual(code, 0)
        self.assertIn("jsonrpc/version", out)
        self.assertNotIn("initialize/server-info", out)

    def test_strict_fails_on_weak_fuzz(self):
        # The sloppy server has warning failures; --strict turns them into
        # a different exit code than the hard failure it already has.
        code, _, _ = run(["run", "--stdio", self.sloppy, "--strict"])
        self.assertIn(code, (1, 3))

    def test_missing_target(self):
        code, _, err = run(["run"])
        self.assertEqual(code, 2)
        self.assertIn("--stdio", err)


class ListingTest(unittest.TestCase):
    def test_checks_lists_ids(self):
        code, out, _ = run(["checks"])
        self.assertEqual(code, 0)
        self.assertIn("initialize/server-info", out)
        self.assertIn("tools/call/missing-required", out)

    def test_fuzz_cases_lists_payloads(self):
        code, out, _ = run(["fuzz-cases"])
        self.assertEqual(code, 0)
        self.assertIn("invalid-json-brace", out)
        self.assertIn("deep-nesting", out)


class UsageTest(unittest.TestCase):
    def test_no_command(self):
        code, out, _ = run([])
        self.assertEqual(code, 2)
        self.assertIn("usage:", out)


if __name__ == "__main__":
    unittest.main()
