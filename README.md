# mcpcheck

[![ci](https://github.com/Coisini-cyber/mcpcheck/actions/workflows/ci.yml/badge.svg)](https://github.com/Coisini-cyber/mcpcheck/actions/workflows/ci.yml)

**Conformance and fuzz checks for MCP servers, in one CI command.**

Model Context Protocol servers now sit between agents and everything they
touch: files, databases, browsers, cloud APIs. Most of them have never been
tested against a client that sends something unexpected.

`mcpcheck` runs 20 spec checks and 28 hostile payloads against any MCP server
over stdio or streamable HTTP. It has no dependencies, no SDK, and no opinion
about how you wrote the server.

```bash
mcpcheck run --stdio "python server.py"
mcpcheck run --url http://127.0.0.1:3000/mcp
```

```text
pass error     initialize/server-info             demo-compliant 1.0.0
pass error     initialize/protocol-version        2025-06-18
pass error     initialize/capabilities            prompts, resources, tools
...
pass error     tools/call/missing-required        rejected with code -32602
pass warning   tools/list/pagination              single page

PASS: 20 checks, 0 error failures, 0 warning failures, 0 fuzz failures
```

Point it at a broken server and it says exactly what is broken:

```text
FAIL error     initialize/protocol-version        protocolVersion '1.0' is not a known MCP version
FAIL error     jsonrpc/notification-silence       server replied to a notification during the handshake
FAIL error     errors/unknown-method              unknown method returned a result instead of an error
FAIL error     tools/list/unique-names            duplicate tool names: add
FAIL warning   tools/list/schema-type             tool 'add' inputSchema.type is 'string', expected 'object'
BAD  invalid-json-brace                           server exited while handling this payload
```

## Try it in ten seconds

```bash
mcpcheck demo -o demo
mcpcheck run --stdio "python demo/compliant_server.py"   # passes
mcpcheck run --stdio "python demo/sloppy_server.py"      # 11 failures
```

The two servers are written to the directory; read them to see what each check
is reacting to.

## What it checks

Every check is a single observable behaviour, with a severity that reflects the
consequence rather than the tool's confidence.

| id | severity | what it means |
| --- | --- | --- |
| `initialize/server-info` | error | `serverInfo.name` and `.version` are non-empty |
| `initialize/protocol-version` | error | `protocolVersion` is a known MCP version |
| `initialize/capabilities` | error | `capabilities` is a non-empty object |
| `initialize/tools-honesty` | error | the `tools` capability matches whether `tools/list` works |
| `jsonrpc/version` | error | responses carry `"jsonrpc": "2.0"` |
| `jsonrpc/id-echo` | error | integer and string ids come back unchanged |
| `jsonrpc/notification-silence` | error | notifications get no reply |
| `errors/unknown-method` | error | unknown methods return `-32601` |
| `errors/invalid-params` | error | bad params return `-32602` |
| `errors/shape` | error | errors carry an integer `code` and a string `message` |
| `tools/list/shape` | error | tools have non-empty names and object schemas |
| `tools/list/unique-names` | error | names are unique and trimmed |
| `tools/list/schema-type` | warning | `inputSchema.type` is `"object"` |
| `tools/list/pagination` | warning | `nextCursor` terminates |
| `tools/call/unknown-tool` | error | unknown tools are rejected, not answered |
| `tools/call/missing-required` | error | required arguments are enforced |
| `tools/call/synthetic` | warning | a synthetic call returns a well-formed `content` array |
| `resources/list` | warning | matches the advertised capability |
| `prompts/list` | warning | matches the advertised capability |
| `performance/ping` | info | ping stays under 2s |

`mcpcheck checks` prints the live list.

Checks that depend on a capability run whenever the server actually supports it,
even if it forgot to advertise it — that mismatch is itself reported by
`initialize/tools-honesty`, and hiding the downstream checks would hide the
more useful failures.

## What it fuzzes

28 payloads, each written as a raw line so the server sees exactly what a
hostile client would send:

- malformed JSON, plain text, a batch array, an empty line
- missing or wrong `jsonrpc`, missing or non-string `method`
- ids that are objects, null, negative, or fractional
- `params` that are strings, arrays, null, or deeply nested
- a 5000-character method name, a 200 KB string, emoji, NUL bytes
- duplicate keys, unknown methods, unknown notifications
- `tools/call` with no params, null params, empty params, or string arguments

After every payload the harness re-syncs with a ping. Outcomes are:

| outcome | meaning |
| --- | --- |
| `ok` | a well-formed JSON-RPC error (or a legitimate result), and the server still answers |
| `weak` | no reply, or a result for clearly invalid input — legal, but lenient |
| `BAD` | the server exited, hung, or answered with invalid JSON-RPC |

`mcpcheck fuzz-cases` prints the list.

## Exit codes

| code | meaning |
| --- | --- |
| `0` | no error-severity failures and no fuzz crashes |
| `1` | an error-severity check failed, or the server crashed under fuzzing |
| `2` | usage error |
| `3` | `--strict` and a warning-severity check or a weak fuzz response failed |

`--strict` is the difference between "spec violations" and "everything I would
want to fix".

## CI

```yaml
name: mcp
on: [push, pull_request]
jobs:
  conformance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install mcpcheck
      - run: mcpcheck run --stdio "python -m my_server" --json conformance.json -o conformance.md
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: mcp-conformance
          path: |
            conformance.json
            conformance.md
```

The Markdown report is designed to be pasted into a PR; the JSON report is
stable enough to diff between releases.

## Design notes

- **No SDK.** The client speaks JSON-RPC 2.0 directly, so a bug in a client
  library cannot mask a bug in the server.
- **Strict parsing.** A response with the wrong `jsonrpc`, both `result` and
  `error`, or a non-object `result` is reported, not repaired.
- **No hidden state between checks.** Each check makes its own request. A check
  that crashes the server is recorded and the run stops rather than reporting
  cascading noise.
- **Honest severity.** Nothing is an error unless a spec-following client would
  actually misbehave.

See [docs/spec-coverage.md](docs/spec-coverage.md) for what is and is not
covered.

## Limitations

- It is a client-side conformance tool, not a security scanner. It does not
  test authorization, sandboxing, or prompt injection.
- HTTP coverage is basic: one POST per message, JSON or event-stream replies.
  OAuth flows are not exercised.
- Batch requests are accepted but not required; MCP makes them optional.
- Sampling, elicitation, and completions are not covered yet.
- Python 3.9+, no third-party packages.

## Development

```bash
python -m unittest discover -s tests -v
python -m compileall -q mcpcheck
```

66 tests, including full runs against a compliant and a deliberately broken
server.

## License

MIT.
