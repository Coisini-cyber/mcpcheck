"""Two MCP servers for the demo: one compliant, one deliberately sloppy.

``mcpcheck demo -o demo`` writes them next to each other. Running the checker
against both is the fastest way to see what a report looks like when it has
something to say.
"""

from __future__ import annotations

import os
from typing import Dict

COMPLIANT_SERVER = r'''"""A minimal, spec-compliant MCP server over stdio.

Used as the positive control in the mcpcheck demo. It implements the handshake,
ping, tools, resources, and prompts, and it returns proper JSON-RPC errors.
"""

import json
import sys

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "add",
        "description": "Add two numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "now",
        "description": "Return a fixed timestamp, for reproducible demos.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def error(request_id, code, message):
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def handle(message):
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params")

    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "demo-compliant", "version": "1.0.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "resources/list":
        return {
            "resources": [
                {
                    "uri": "demo://readme",
                    "name": "readme",
                    "mimeType": "text/plain",
                }
            ]
        }
    if method == "prompts/list":
        return {
            "prompts": [
                {"name": "summarise", "description": "Summarise a document."}
            ]
        }
    if method == "tools/call":
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            raise ValueError("name must be a string")
        if name == "add":
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
            if "a" not in arguments or "b" not in arguments:
                raise KeyError("a and b are required")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": str(float(arguments["a"]) + float(arguments["b"])),
                    }
                ]
            }
        if name == "now":
            return {"content": [{"type": "text", "text": "2026-01-01T00:00:00Z"}]}
        raise LookupError("unknown tool: %s" % name)
    raise NotImplementedError("unknown method: %s" % method)


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except ValueError:
        error(None, -32700, "parse error")
        continue
    if not isinstance(message, dict):
        error(None, -32600, "invalid request")
        continue

    request_id = message.get("id")
    if message.get("jsonrpc") != "2.0":
        error(request_id, -32600, "jsonrpc must be '2.0'")
        continue
    if not isinstance(message.get("method"), str):
        error(request_id, -32600, "method must be a string")
        continue
    if message.get("params") is not None and not isinstance(message.get("params"), dict):
        error(request_id, -32602, "params must be an object")
        continue
    if request_id is None:
        # A notification: never reply.
        continue

    try:
        result = handle(message)
    except ValueError as exc:
        error(request_id, -32602, str(exc))
    except KeyError as exc:
        error(request_id, -32602, "missing argument: %s" % exc)
    except LookupError as exc:
        error(request_id, -32602, str(exc))
    except NotImplementedError as exc:
        error(request_id, -32601, str(exc))
    except Exception as exc:
        error(request_id, -32603, "internal error: %s" % exc)
    else:
        send({"jsonrpc": "2.0", "id": request_id, "result": result})
'''

SLOPPY_SERVER = r'''"""A deliberately broken MCP server, used as the negative control.

Every defect here is one that real servers have shipped: a wrong protocol
version, an empty capabilities object, a reply to a notification, a wrong error
code, duplicate tool names, ignored required arguments, and a crash on
malformed input.
"""

import json
import sys

TOOLS = [
    {
        "name": "add",
        "description": "Add two numbers.",
        "inputSchema": {
            "type": "string",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "add",
        "description": "A duplicate.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except ValueError:
        # Defect: a parse error kills the process.
        sys.exit(1)

    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "1.0",
                    "capabilities": {},
                    "serverInfo": {"name": "", "version": ""},
                },
            }
        )
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": request_id, "result": {}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        # Defect: required arguments are ignored and the error is a string.
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": "ok"}]},
            }
        )
    elif request_id is None:
        # Defect: notifications get a reply.
        send({"jsonrpc": "2.0", "id": None, "result": {}})
    else:
        # Defect: unknown methods return a result instead of -32601.
        send({"jsonrpc": "2.0", "id": request_id, "result": {}})
'''


def write_demo(directory: str) -> Dict[str, str]:
    os.makedirs(directory, exist_ok=True)
    compliant = os.path.join(directory, "compliant_server.py")
    sloppy = os.path.join(directory, "sloppy_server.py")
    with open(compliant, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(COMPLIANT_SERVER)
    with open(sloppy, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(SLOPPY_SERVER)
    return {"compliant": compliant, "sloppy": sloppy}
