"""mcpcheck command line interface."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence

from . import __version__
from .checks import CHECKS, run_checks
from .demo import write_demo
from .fuzz import build_cases, run_fuzz
from .report import Report, render_console, render_json, render_markdown


def _write(text: str, path: Optional[str]) -> None:
    if path:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print("wrote %s" % path, file=sys.stderr)
    else:
        sys.stdout.write(text)


def _target(args: argparse.Namespace) -> str:
    if args.stdio:
        return args.stdio
    if args.url:
        return args.url
    raise SystemExit("error: one of --stdio or --url is required")


def cmd_run(args: argparse.Namespace) -> int:
    target = _target(args)
    checks = []
    fuzz = []
    if args.checks:
        checks = run_checks(
            command=args.stdio, url=args.url, timeout=args.timeout, only=args.only
        )
    if args.fuzz:
        fuzz = run_fuzz(
            command=args.stdio,
            url=args.url,
            timeout=args.timeout,
            case_timeout=args.case_timeout,
            only=args.only,
        )
    report = Report(target=target, checks=checks, fuzz=fuzz)

    if args.json:
        _write(render_json(report), args.json)
    if args.report:
        _write(render_markdown(report, title=args.title), args.report)
    if not args.quiet:
        if args.json or args.report:
            print(render_console(report), file=sys.stderr)
        else:
            print(render_console(report))

    if report.errors_failed or report.fuzz_bad:
        return 1
    if args.strict and (report.warnings_failed or report.fuzz_weak):
        return 3
    return 0


def cmd_checks(args: argparse.Namespace) -> int:
    print("%-34s %-8s %s" % ("id", "severity", "title"))
    for check in CHECKS:
        print("%-34s %-8s %s" % (check.id, check.severity, check.title))
    return 0


def cmd_fuzz_cases(args: argparse.Namespace) -> int:
    print("%-28s %-6s %s" % ("id", "reply", "description"))
    for case in build_cases():
        print(
            "%-28s %-6s %s"
            % (case.id, "yes" if case.expects_response else "no", case.description)
        )
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    paths = write_demo(args.out or "demo")
    print("wrote %s" % paths["compliant"])
    print("wrote %s" % paths["sloppy"])
    print("")
    print("next:")
    print('  mcpcheck run --stdio "python %s"' % paths["compliant"])
    print('  mcpcheck run --stdio "python %s"' % paths["sloppy"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcpcheck",
        description="Conformance and fuzz checks for Model Context Protocol servers.",
    )
    parser.add_argument("--version", action="version", version="mcpcheck %s" % __version__)
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="run conformance checks and fuzzing")
    target = run_parser.add_mutually_exclusive_group()
    target.add_argument("--stdio", help='command that starts the server, e.g. "python server.py"')
    target.add_argument("--url", help="streamable HTTP endpoint")
    run_parser.add_argument("--checks", action=argparse.BooleanOptionalAction, default=True)
    run_parser.add_argument("--fuzz", action=argparse.BooleanOptionalAction, default=True)
    run_parser.add_argument("--only", action="append", help="run only these check or fuzz ids")
    run_parser.add_argument("--timeout", type=float, default=15.0, help="per-request timeout")
    run_parser.add_argument(
        "--case-timeout", type=float, default=0.4, help="how long to wait for a fuzz reply"
    )
    run_parser.add_argument("--json", help="write a JSON report here")
    run_parser.add_argument("-o", "--report", help="write a Markdown report here")
    run_parser.add_argument("--title", default="MCP conformance report")
    run_parser.add_argument(
        "--strict",
        action="store_true",
        help="also fail on warning-severity findings and weak fuzz responses",
    )
    run_parser.add_argument("--quiet", action="store_true")
    run_parser.set_defaults(func=cmd_run)

    checks_parser = subparsers.add_parser("checks", help="list conformance checks")
    checks_parser.set_defaults(func=cmd_checks)

    cases_parser = subparsers.add_parser("fuzz-cases", help="list fuzz payloads")
    cases_parser.set_defaults(func=cmd_fuzz_cases)

    demo_parser = subparsers.add_parser("demo", help="write a compliant and a broken server")
    demo_parser.add_argument("-o", "--out", help="directory (default: demo)")
    demo_parser.set_defaults(func=cmd_demo)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        return int(args.func(args))
    except SystemExit as error:
        # argparse uses SystemExit for usage errors; keep the message on stderr.
        if isinstance(error.code, str):
            print(error.code, file=sys.stderr)
            return 2
        raise
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
