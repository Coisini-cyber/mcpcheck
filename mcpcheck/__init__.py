"""mcpcheck: conformance and fuzz checks for Model Context Protocol servers.

    >>> from mcpcheck import run_checks, run_fuzz
    >>> checks = run_checks(command="python server.py")
    >>> fuzz = run_fuzz(command="python server.py")

The checker speaks the protocol directly: no SDK, no framework, no network
unless you point it at an HTTP endpoint. It answers two questions a server
author cares about in CI:

- does this server follow the MCP spec closely enough for a real client?
- does it stay alive and keep answering when a client sends garbage?
"""

from __future__ import annotations

__version__ = "0.1.0"

from .checks import (  # noqa: E402
    CHECKS,
    CheckResult,
    run_checks,
)
from .client import (  # noqa: E402
    HTTPTransport,
    ProtocolError,
    Response,
    StdioTransport,
    TransportError,
)
from .fuzz import FuzzResult, build_cases, run_fuzz  # noqa: E402
from .report import Report, render_console, render_json, render_markdown  # noqa: E402
from .session import Session, SessionError  # noqa: E402

__all__ = [
    "__version__",
    "CHECKS",
    "CheckResult",
    "FuzzResult",
    "HTTPTransport",
    "ProtocolError",
    "Report",
    "Response",
    "Session",
    "SessionError",
    "StdioTransport",
    "TransportError",
    "build_cases",
    "render_console",
    "render_json",
    "render_markdown",
    "run_checks",
    "run_fuzz",
]
