# What mcpcheck covers

This page records which parts of the Model Context Protocol the checker
exercises, and which it deliberately leaves alone. It is meant to keep the
README honest as the protocol moves.

Protocol revision referenced: `2025-06-18`, with `2025-03-26` and `2024-11-05`
accepted as negotiated versions.

## Transport

| area | status | notes |
| --- | --- | --- |
| stdio framing | covered | newline-delimited JSON, one message per line |
| streamable HTTP | covered | one POST per message; JSON and `text/event-stream` replies |
| session id header | partial | the client echoes `Mcp-Session-Id` if the server sets it |
| legacy HTTP+SSE | not covered | the two-endpoint transport is deprecated |
| OAuth / authorization | not covered | out of scope |
| TLS configuration | not covered | uses the platform trust store |

## Lifecycle

| area | status | notes |
| --- | --- | --- |
| `initialize` request shape | covered | the client sends a protocol version, empty capabilities, client info |
| version negotiation | covered | the response must be a known version |
| `notifications/initialized` | covered | must not produce a reply |
| `ping` | covered | used for latency and liveness |
| shutdown / `exit` | not covered | the harness terminates the process |

## Tools

| area | status | notes |
| --- | --- | --- |
| `tools/list` shape | covered | array of objects, non-empty names |
| `inputSchema` | covered | must be an object; `type` should be `"object"` |
| pagination | covered | `nextCursor` must terminate within 20 pages |
| `tools/call` success | covered | `content` must be an array of typed entries |
| `tools/call` unknown tool | covered | must be a JSON-RPC error |
| required arguments | covered | enforced for the first tool that declares them |
| `isError` results | partial | accepted, not distinguished from protocol errors |
| structured content | not covered | `structuredContent` is not validated |
| output schemas | not covered | `outputSchema` is not validated |

## Resources and prompts

| area | status | notes |
| --- | --- | --- |
| `resources/list` | covered | array of objects with a string `uri` |
| `resources/templates/list` | not covered | |
| `resources/read` | not covered | needs a real resource |
| `prompts/list` | covered | array of objects with a string `name` |
| `prompts/get` | not covered | needs real arguments |
| subscriptions | not covered | requires a long-lived stream |

## JSON-RPC

| area | status | notes |
| --- | --- | --- |
| `jsonrpc: "2.0"` | covered | required on every response |
| id echo | covered | integer and string ids |
| `result` xor `error` | covered | both or neither is a protocol error |
| error shape | covered | integer `code`, string `message` |
| standard error codes | covered | `-32601` and `-32602` are asserted where the spec requires them |
| batch requests | partial | accepted if the server supports them, never required |
| `-32700` parse errors | observed | the fuzz run records whether malformed JSON is rejected |
| cancellation | not covered | `notifications/cancelled` is not exercised |

## Fuzzing

The fuzz suite targets the JSON-RPC surface, not the domain logic of your tools.
It asks whether the server keeps running and keeps answering after a malformed
message. It does not attempt to find semantic bugs in tool implementations,
resource path traversal, or injection into downstream systems.

That is a deliberate boundary: a conformance tool that also claims to be a
security scanner tends to do neither job well.
