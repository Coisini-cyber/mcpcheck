"""A deliberately broken MCP server, used as the negative control.

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
