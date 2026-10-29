"""Conformance checks for an MCP server.

Each check is a small function that inspects one observable behaviour. Checks
never abort the run: a failure is recorded and the next check proceeds with a
fresh request, so one broken endpoint does not hide the rest of the report.

Severity is about consequence, not confidence:

- ``error``: a client following the spec will misbehave.
- ``warning``: legal but fragile, or a capability that is advertised and broken.
- ``info``: measurement, not a verdict.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .client import ProtocolError, TransportError
from .session import SUPPORTED_PROTOCOL_VERSIONS, Session, SessionError

ERROR = "error"
WARNING = "warning"
INFO = "info"

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


@dataclass
class CheckResult:
    id: str
    title: str
    severity: str
    status: str
    detail: str = ""
    duration_ms: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "status": self.status,
            "detail": self.detail,
            "duration_ms": round(self.duration_ms, 1),
        }


@dataclass
class Check:
    id: str
    title: str
    severity: str
    func: Callable[[Session], "Outcome"]
    requires: str = ""  # "tools", "resources", or "prompts"


@dataclass
class Outcome:
    status: str
    detail: str = ""


def _ok(detail: str = "") -> Outcome:
    return Outcome(PASS, detail)


def _fail(detail: str) -> Outcome:
    return Outcome(FAIL, detail)


def _skip(detail: str) -> Outcome:
    return Outcome(SKIP, detail)


def _synthetic_arguments(schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build a plausible argument object from a JSON Schema."""
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    required = schema.get("required")
    names = required if isinstance(required, list) and required else list(properties)[:3]
    arguments: Dict[str, Any] = {}
    for name in names:
        spec = properties.get(name)
        if not isinstance(spec, dict):
            arguments[name] = "test"
            continue
        kind = spec.get("type")
        if kind == "string":
            arguments[name] = "test"
        elif kind in ("number", "integer"):
            arguments[name] = 1
        elif kind == "boolean":
            arguments[name] = True
        elif kind == "array":
            arguments[name] = []
        elif kind == "object":
            arguments[name] = {}
        else:
            arguments[name] = "test"
    return arguments


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


def check_server_info(session: Session) -> Outcome:
    info = session.server_info
    if not info:
        return _fail("initialize result has no serverInfo object")
    name = info.get("name")
    version = info.get("version")
    if not isinstance(name, str) or not name.strip():
        return _fail("serverInfo.name must be a non-empty string, got %r" % (name,))
    if not isinstance(version, str) or not version.strip():
        return _fail("serverInfo.version must be a non-empty string, got %r" % (version,))
    return _ok("%s %s" % (name, version))


def check_protocol_version(session: Session) -> Outcome:
    version = session.protocol_version
    if not version:
        return _fail("initialize result has no protocolVersion string")
    if version not in SUPPORTED_PROTOCOL_VERSIONS:
        return _fail(
            "protocolVersion %r is not a known MCP version (%s)"
            % (version, ", ".join(SUPPORTED_PROTOCOL_VERSIONS))
        )
    if version != session.requested_version:
        return _ok("negotiated %s (requested %s)" % (version, session.requested_version))
    return _ok(version)


def check_capabilities(session: Session) -> Outcome:
    capabilities = session.init_result.get("capabilities")
    if not isinstance(capabilities, dict):
        return _fail("initialize result has no capabilities object")
    if not capabilities:
        return _fail("capabilities is empty: the server advertises nothing")
    known = {"tools", "resources", "prompts", "logging", "completions", "experimental"}
    unknown = sorted(set(capabilities) - known)
    if unknown:
        return _ok("advertises %s (unknown keys: %s)" % (", ".join(sorted(capabilities)), ", ".join(unknown)))
    return _ok(", ".join(sorted(capabilities)))


def check_tools_capability_honesty(session: Session) -> Outcome:
    advertised = "tools" in session.capabilities
    response = session.request("tools/list", {})
    works = not response.is_error
    if advertised and not works:
        return _fail("advertises tools but tools/list returns an error")
    if not advertised and works:
        return _fail("tools/list works but the tools capability is not advertised")
    if not advertised and not works:
        return _skip("tools are not supported")
    return _ok("capability and tools/list agree")


def check_tools_shape(session: Session) -> Outcome:
    response = session.request("tools/list", {})
    if response.is_error:
        return _skip("tools/list returned an error: %s" % response.error)
    tools = (response.result or {}).get("tools")
    if not isinstance(tools, list):
        return _fail("tools/list result.tools must be an array")
    if not tools:
        return _ok("no tools, empty array")
    for tool in tools:
        if not isinstance(tool, dict):
            return _fail("tool entries must be objects, got %r" % type(tool).__name__)
        name = tool.get("name")
        if not isinstance(name, str) or not name.strip():
            return _fail("tool entry has no non-empty name: %r" % (tool,))
        if "inputSchema" in tool and not isinstance(tool["inputSchema"], dict):
            return _fail("tool %r inputSchema must be an object" % name)
    return _ok("%d tool(s)" % len(tools))


def check_tools_unique_names(session: Session) -> Outcome:
    tools = session.tools
    if not tools:
        return _skip("no tools")
    names = [tool.get("name") for tool in tools]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        return _fail("duplicate tool names: %s" % ", ".join(str(name) for name in duplicates))
    spaced = [name for name in names if isinstance(name, str) and name != name.strip()]
    if spaced:
        return _fail("tool names have leading or trailing whitespace: %r" % spaced)
    return _ok("%d unique name(s)" % len(names))


def check_tools_schema_type(session: Session) -> Outcome:
    tools = session.tools
    if not tools:
        return _skip("no tools")
    missing = []
    for tool in tools:
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            missing.append(tool.get("name"))
            continue
        if schema.get("type") != "object":
            return _fail(
                "tool %r inputSchema.type is %r, expected 'object'"
                % (tool.get("name"), schema.get("type"))
            )
    if missing:
        return _fail("tools without an inputSchema object: %r" % missing)
    return _ok("all schemas are objects")


def check_tools_pagination(session: Session) -> Outcome:
    response = session.request("tools/list", {})
    if response.is_error:
        return _skip("tools/list returned an error")
    result = response.result or {}
    cursor = result.get("nextCursor")
    if cursor is None:
        return _ok("single page")
    if not isinstance(cursor, str) or not cursor:
        return _fail("nextCursor must be a non-empty string, got %r" % (cursor,))
    seen = set()
    pages = 1
    while isinstance(cursor, str) and cursor:
        if cursor in seen:
            return _fail("nextCursor %r repeated: pagination does not terminate" % cursor)
        seen.add(cursor)
        page = session.request("tools/list", {"cursor": cursor})
        if page.is_error:
            return _fail("following nextCursor returned an error: %s" % page.error)
        cursor = (page.result or {}).get("nextCursor")
        pages += 1
        if pages > 20:
            return _fail("more than 20 pages: pagination does not terminate")
    return _ok("%d page(s)" % pages)


def check_unknown_method(session: Session) -> Outcome:
    response = session.request("definitely/not-a-method", {})
    if not response.is_error:
        return _fail("unknown method returned a result instead of an error")
    code = response.error_code
    if code != -32601:
        return _fail("unknown method should use -32601, got %r" % (code,))
    return _ok("-32601")


def check_invalid_params(session: Session) -> Outcome:
    response = session.request("tools/call", {"name": 123, "arguments": "not-an-object"})
    if not response.is_error:
        return _fail("tools/call with a non-string name returned a result")
    code = response.error_code
    if code not in (-32602, -32601):
        return _fail("invalid params should use -32602, got %r" % (code,))
    return _ok("code %d" % code)


def check_unknown_tool(session: Session) -> Outcome:
    response = session.call_tool("definitely-not-a-tool", {})
    if not response.is_error:
        return _fail("calling an unknown tool returned a successful result")
    code = response.error_code
    if code not in (-32602, -32601):
        return _fail("unknown tool should use -32602 or -32601, got %r" % (code,))
    return _ok("code %d" % code)


def check_error_shape(session: Session) -> Outcome:
    response = session.request("definitely/not-a-method", {})
    if not response.is_error:
        return _fail("no error to inspect")
    error = response.error or {}
    code = error.get("code")
    message = error.get("message")
    if not isinstance(code, int):
        return _fail("error.code must be an integer, got %r" % (code,))
    if not isinstance(message, str) or not message:
        return _fail("error.message must be a non-empty string, got %r" % (message,))
    return _ok("code %d, message present" % code)


def check_missing_required_arguments(session: Session) -> Outcome:
    tools = session.tools
    target = None
    for tool in tools:
        schema = tool.get("inputSchema") or {}
        required = schema.get("required")
        if isinstance(required, list) and required:
            target = tool
            break
    if target is None:
        return _skip("no tool declares required arguments")
    response = session.call_tool(str(target.get("name")), {})
    if not response.is_error:
        return _fail(
            "tool %r accepted a call with its required arguments missing"
            % target.get("name")
        )
    code = response.error_code
    if code not in (-32602, -32603):
        return _fail(
            "missing arguments should use -32602 or -32603, got %r" % (code,)
        )
    return _ok("rejected with code %d" % code)


def check_synthetic_call(session: Session) -> Outcome:
    tools = session.tools
    if not tools:
        return _skip("no tools")
    target = tools[0]
    schema = target.get("inputSchema") or {}
    arguments = _synthetic_arguments(schema)
    if arguments is None:
        return _skip("could not build arguments")
    response = session.call_tool(str(target.get("name")), arguments)
    if response.is_error:
        # A domain error is acceptable; a protocol error is not.
        code = response.error_code
        if code is not None:
            return _fail(
                "tool %r rejected synthetic arguments with a protocol error (%d)"
                % (target.get("name"), code)
            )
        return _ok("domain error, as expected for synthetic input")
    result = response.result or {}
    content = result.get("content")
    if not isinstance(content, list):
        return _fail("tools/call result.content must be an array")
    if not content:
        return _ok("empty content array")
    first = content[0]
    if not isinstance(first, dict):
        return _fail("content entries must be objects")
    if "type" not in first:
        return _fail("content entry has no type")
    return _ok("content type %r" % first.get("type"))


def check_id_echo(session: Session) -> Outcome:
    for value in (42, "string-id"):
        response = session.transport.send(
            {"jsonrpc": "2.0", "id": value, "method": "ping"}
        )
        if response.id != value:
            return _fail(
                "request id %r came back as %r" % (value, response.id)
            )
    return _ok("integer and string ids echoed")


def check_notification_silence(session: Session) -> Outcome:
    if session.stray_after_notification:
        return _fail(
            "server replied to a notification during the handshake: %r"
            % session.stray_after_notification[0][:160]
        )
    session.notify("notifications/initialized")
    if session.stray_after_notification:
        return _fail(
            "server replied to a notification: %r"
            % session.stray_after_notification[0][:160]
        )
    response = session.request("ping")
    sent = session.transport.last_request_id
    if response.id != sent:
        return _fail(
            "server replied to a notification: got id %r while waiting for %r"
            % (response.id, sent)
        )
    if response.is_error:
        return _fail("ping after a notification returned an error")
    return _ok("notifications produce no reply")


def check_jsonrpc_version(session: Session) -> Outcome:
    response = session.request("ping")
    if response.raw.get("jsonrpc") != "2.0":
        return _fail("response jsonrpc is %r" % response.raw.get("jsonrpc"))
    return _ok("2.0")


def check_resources(session: Session) -> Outcome:
    response = session.request("resources/list", {})
    if response.is_error:
        return _fail("resources capability is advertised but resources/list errors")
    resources = (response.result or {}).get("resources")
    if not isinstance(resources, list):
        return _fail("resources/list result.resources must be an array")
    for resource in resources:
        if not isinstance(resource, dict):
            return _fail("resource entries must be objects")
        if not isinstance(resource.get("uri"), str):
            return _fail("resource entry has no string uri")
    return _ok("%d resource(s)" % len(resources))


def check_prompts(session: Session) -> Outcome:
    response = session.request("prompts/list", {})
    if response.is_error:
        return _fail("prompts capability is advertised but prompts/list errors")
    prompts = (response.result or {}).get("prompts")
    if not isinstance(prompts, list):
        return _fail("prompts/list result.prompts must be an array")
    for prompt in prompts:
        if not isinstance(prompt, dict):
            return _fail("prompt entries must be objects")
        if not isinstance(prompt.get("name"), str):
            return _fail("prompt entry has no string name")
    return _ok("%d prompt(s)" % len(prompts))


def check_latency(session: Session) -> Outcome:
    samples = []
    for _ in range(3):
        started = time.monotonic()
        session.request("ping")
        samples.append((time.monotonic() - started) * 1000)
    average = sum(samples) / len(samples)
    if average > 2000:
        return _fail("ping averages %.0f ms, which will dominate a chat turn" % average)
    return _ok("ping averages %.0f ms" % average)


CHECKS: List[Check] = [
    Check("initialize/server-info", "serverInfo is present and complete", ERROR, check_server_info),
    Check("initialize/protocol-version", "protocolVersion is a known MCP version", ERROR, check_protocol_version),
    Check("initialize/capabilities", "capabilities is a non-empty object", ERROR, check_capabilities),
    Check("initialize/tools-honesty", "tools capability matches tools/list", ERROR, check_tools_capability_honesty),
    Check("jsonrpc/version", "responses use jsonrpc 2.0", ERROR, check_jsonrpc_version),
    Check("jsonrpc/id-echo", "response ids echo the request id", ERROR, check_id_echo),
    Check("jsonrpc/notification-silence", "notifications produce no reply", ERROR, check_notification_silence),
    Check("errors/unknown-method", "unknown methods return -32601", ERROR, check_unknown_method),
    Check("errors/invalid-params", "invalid params return -32602", ERROR, check_invalid_params),
    Check("errors/shape", "errors carry an integer code and a message", ERROR, check_error_shape),
    Check("tools/list/shape", "tools/list returns well-formed tool objects", ERROR, check_tools_shape, "tools"),
    Check("tools/list/unique-names", "tool names are unique and trimmed", ERROR, check_tools_unique_names, "tools"),
    Check("tools/list/schema-type", "inputSchema is an object schema", WARNING, check_tools_schema_type, "tools"),
    Check("tools/list/pagination", "nextCursor terminates", WARNING, check_tools_pagination, "tools"),
    Check("tools/call/unknown-tool", "unknown tools are rejected", ERROR, check_unknown_tool, "tools"),
    Check("tools/call/missing-required", "required arguments are enforced", ERROR, check_missing_required_arguments, "tools"),
    Check("tools/call/synthetic", "a synthetic call returns content", WARNING, check_synthetic_call, "tools"),
    Check("resources/list", "resources/list matches its capability", WARNING, check_resources, "resources"),
    Check("prompts/list", "prompts/list matches its capability", WARNING, check_prompts, "prompts"),
    Check("performance/ping", "ping latency is acceptable", INFO, check_latency),
]


def _requirement_met(session: Session, requirement: str, cache: Dict[str, bool]) -> bool:
    """Whether a capability is usable, advertised or merely working."""
    if not requirement:
        return True
    if requirement in cache:
        return cache[requirement]
    method = {
        "tools": "tools/list",
        "resources": "resources/list",
        "prompts": "prompts/list",
    }.get(requirement)
    supported = requirement in session.capabilities
    if not supported and method:
        try:
            supported = not session.request(method, {}).is_error
        except (ProtocolError, TransportError):
            supported = False
    cache[requirement] = supported
    return supported


def run_checks(
    command: Optional[str] = None,
    url: Optional[str] = None,
    timeout: float = 15.0,
    only: Optional[List[str]] = None,
) -> List[CheckResult]:
    results: List[CheckResult] = []
    try:
        session = Session(command=command, url=url, timeout=timeout).open()
    except SessionError as error:
        return [
            CheckResult(
                id="initialize/handshake",
                title="the server completes the MCP handshake",
                severity=ERROR,
                status=FAIL,
                detail=str(error),
            )
        ]

    try:
        support_cache: Dict[str, bool] = {}
        for check in CHECKS:
            if only and check.id not in only:
                continue
            if not _requirement_met(session, check.requires, support_cache):
                results.append(
                    CheckResult(
                        id=check.id,
                        title=check.title,
                        severity=check.severity,
                        status=SKIP,
                        detail="the server does not support %s" % check.requires,
                    )
                )
                continue
            started = time.monotonic()
            try:
                outcome = check.func(session)
            except (ProtocolError, TransportError) as error:
                outcome = Outcome(FAIL, "%s: %s" % (type(error).__name__, error))
            except Exception as error:  # a broken server must not crash the tool
                outcome = Outcome(FAIL, "unexpected %s: %s" % (type(error).__name__, error))
            results.append(
                CheckResult(
                    id=check.id,
                    title=check.title,
                    severity=check.severity,
                    status=outcome.status,
                    detail=outcome.detail,
                    duration_ms=(time.monotonic() - started) * 1000,
                )
            )
    finally:
        session.close()
    return results
