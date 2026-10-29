"""Fuzz an MCP server's JSON-RPC surface.

The goal is not to find logic bugs. It is to answer one question a CI pipeline
actually cares about: *if a client sends garbage, does this server stay alive
and keep answering?*

Every payload is written as a raw line, bypassing the client's own encoding, so
the server sees exactly the bytes a hostile or buggy client would send. After
each payload the harness re-syncs with a ping; a server that dies, hangs, or
answers with invalid JSON-RPC is recorded as a failure.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .client import ProtocolError, Response, TransportError, parse_response
from .session import Session, SessionError

OK = "ok"
WEAK = "weak"
BAD = "bad"

# Outcome ranking, used for the summary and the exit code.
RANK = {OK: 0, WEAK: 1, BAD: 2}


@dataclass
class FuzzCase:
    id: str
    description: str
    payload: str
    expects_response: bool = True
    expects_error: bool = True


@dataclass
class FuzzResult:
    id: str
    description: str
    outcome: str
    detail: str = ""
    duration_ms: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "outcome": self.outcome,
            "detail": self.detail,
            "duration_ms": round(self.duration_ms, 1),
        }


def _deep_object(depth: int) -> Dict[str, Any]:
    node: Dict[str, Any] = {"leaf": True}
    for _ in range(depth):
        node = {"next": node}
    return node


def build_cases() -> List[FuzzCase]:
    cases: List[FuzzCase] = [
        FuzzCase("invalid-json-brace", "a single brace", "{"),
        FuzzCase("invalid-json-text", "plain text instead of JSON", "this is not json"),
        FuzzCase("json-array", "a batch array instead of an object", '[{"jsonrpc":"2.0","id":1,"method":"ping"}]', expects_error=False),
        FuzzCase("missing-jsonrpc", "no jsonrpc field", '{"id":1,"method":"ping"}'),
        FuzzCase("wrong-jsonrpc", "jsonrpc 1.0", '{"jsonrpc":"1.0","id":1,"method":"ping"}'),
        FuzzCase("missing-method", "no method field", '{"jsonrpc":"2.0","id":1}'),
        FuzzCase("method-not-string", "method is a number", '{"jsonrpc":"2.0","id":1,"method":123}'),
        FuzzCase("id-object", "id is an object", '{"jsonrpc":"2.0","id":{"a":1},"method":"ping"}', expects_error=False),
        FuzzCase("params-string", "params is a string", '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":"x"}'),
        FuzzCase("params-array", "params is an array", '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":[]}', expects_error=False),
        FuzzCase("unknown-method", "an unknown method", '{"jsonrpc":"2.0","id":1,"method":"no/such/method"}'),
        FuzzCase("long-method", "a 5000 character method name", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "a" * 5000})),
        FuzzCase(
            "huge-params",
            "a 200 KB string in params",
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"q": "x" * 200000}}),
            expects_error=False,
        ),
        FuzzCase(
            "deep-nesting",
            "200 levels of nesting in params",
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": _deep_object(200)}),
        ),
        FuzzCase(
            "unicode-method",
            "an emoji method name",
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "\U0001f600/\U0001f4a5"}),
        ),
        FuzzCase(
            "control-chars",
            "a NUL byte in params",
            '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"x","arguments":{"a":"\\u0000"}}}',
        ),
        FuzzCase(
            "duplicate-keys",
            "a duplicate jsonrpc key",
            '{"jsonrpc":"2.0","jsonrpc":"2.0","id":1,"method":"ping"}',
            expects_error=False,
        ),
        FuzzCase(
            "null-id",
            "a null id (treat as a notification)",
            '{"jsonrpc":"2.0","id":null,"method":"ping"}',
            expects_response=False,
            expects_error=False,
        ),
        FuzzCase("negative-id", "a negative id", '{"jsonrpc":"2.0","id":-1,"method":"ping"}', expects_error=False),
        FuzzCase("float-id", "a fractional id", '{"jsonrpc":"2.0","id":1.5,"method":"ping"}', expects_error=False),
        FuzzCase("tools-call-no-params", "tools/call without params", '{"jsonrpc":"2.0","id":1,"method":"tools/call"}'),
        FuzzCase("tools-call-null-params", "tools/call with null params", '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":null}'),
        FuzzCase("tools-call-empty-params", "tools/call with empty params", '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{}}'),
        FuzzCase(
            "tools-call-args-string",
            "tool arguments as a string",
            '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"x","arguments":"y"}}',
        ),
        FuzzCase(
            "initialize-null-params",
            "initialize with null params",
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":null}',
            expects_error=False,
        ),
        FuzzCase(
            "notification-unknown",
            "an unknown notification",
            '{"jsonrpc":"2.0","method":"no/such/notification"}',
            expects_response=False,
            expects_error=False,
        ),
        FuzzCase("empty-line", "an empty line", "", expects_response=False, expects_error=False),
        FuzzCase("whitespace-line", "a whitespace-only line", "   ", expects_response=False, expects_error=False),
    ]
    return cases


def _drain(session: Session, timeout: float = 0.15) -> List[str]:
    lines: List[str] = []
    transport = session.transport
    while True:
        try:
            lines.append(transport.read_raw(timeout=timeout))
        except TransportError as error:
            if "exited" in str(error):
                raise
            break
    return lines


def _resync(session: Session) -> bool:
    """Drain stray output, then confirm the server still answers a ping."""
    try:
        _drain(session)
    except TransportError:
        return False
    for _ in range(4):
        try:
            response = session.request("ping")
        except (TransportError, ProtocolError):
            return False
        if response.id == session.transport.last_request_id:
            return not response.is_error
        # A late reply to an earlier payload; keep reading.
    return False


def _validate(line: str, case: FuzzCase) -> str:
    text = line.strip()
    if text.startswith("["):
        # Batch responses are optional in MCP; accept an array of messages as
        # long as each element is well formed.
        try:
            items = json.loads(text)
        except ValueError as error:
            return "invalid JSON: %s" % error
        if not isinstance(items, list) or not items:
            return "batch response is empty"
        return ""
    try:
        response = parse_response(line, None)
    except ProtocolError as error:
        return "invalid JSON-RPC: %s" % error
    if response.is_error:
        error = response.error or {}
        if not isinstance(error.get("code"), int):
            return "error.code is not an integer: %r" % (error.get("code"),)
        if not isinstance(error.get("message"), str):
            return "error.message is not a string: %r" % (error.get("message"),)
        return ""
    if case.expects_error:
        return "accepted the payload and returned a result"
    return ""


def run_fuzz(
    command: Optional[str] = None,
    url: Optional[str] = None,
    timeout: float = 15.0,
    case_timeout: float = 0.4,
    only: Optional[List[str]] = None,
) -> List[FuzzResult]:
    try:
        session = Session(command=command, url=url, timeout=timeout).open()
    except SessionError as error:
        return [
            FuzzResult(
                id="handshake",
                description="the server completes the MCP handshake",
                outcome=BAD,
                detail=str(error),
            )
        ]

    results: List[FuzzResult] = []
    try:
        for case in build_cases():
            if only and case.id not in only:
                continue
            started = time.monotonic()
            outcome = OK
            detail = ""
            try:
                session.transport.write_raw(case.payload)
            except TransportError as error:
                results.append(
                    FuzzResult(
                        id=case.id,
                        description=case.description,
                        outcome=BAD,
                        detail="write failed: %s" % error,
                        duration_ms=(time.monotonic() - started) * 1000,
                    )
                )
                break

            if case.expects_response:
                try:
                    line = session.transport.read_raw(timeout=case_timeout)
                except TransportError as error:
                    if "exited" in str(error):
                        results.append(
                            FuzzResult(
                                id=case.id,
                                description=case.description,
                                outcome=BAD,
                                detail="server exited: %s" % error,
                                duration_ms=(time.monotonic() - started) * 1000,
                            )
                        )
                        break
                    outcome = WEAK
                    detail = "no response within %.1fs" % case_timeout
                else:
                    problem = _validate(line, case)
                    if problem:
                        outcome = WEAK if "accepted" in problem else BAD
                        detail = problem

            if outcome != BAD:
                if not _resync(session):
                    outcome = BAD
                    if not session.alive():
                        detail = "server exited while handling this payload"
                    else:
                        detail = (detail + "; " if detail else "") + "server did not answer a ping afterwards"

            results.append(
                FuzzResult(
                    id=case.id,
                    description=case.description,
                    outcome=outcome,
                    detail=detail,
                    duration_ms=(time.monotonic() - started) * 1000,
                )
            )
            if outcome == BAD:
                break
    finally:
        session.close()
    return results
