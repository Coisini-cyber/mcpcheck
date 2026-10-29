"""A minimal, spec-compliant MCP server over stdio.

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
