# MCP gate

Exeat can sit on stdio in front of tools. The agent process talks MCP to
Exeat. Exeat decides, then either answers with the denial or
forwards the call.

No model is required. Point any MCP client at the process, including one that
runs a local model. Exeat never calls a model and never reads a prompt.

## Builtin finance tools

```bash
python3 -m exeat mcp --example --agent finance-assistant
```

This serves the simulated ledger, files, and mail from the finance example.
`http.get` returns canned data. Nothing opens a socket.

A client config, with the command adjusted to your checkout:

```json
{
  "mcpServers": {
    "finance": {
      "command": "python3",
      "args": ["-m", "exeat", "mcp", "--example", "--agent", "finance-assistant"]
    }
  }
}
```

`tools/list` advertises only tools the agent is granted and the backend can
run. `tools/call` is decided again. A denied or held call comes back as a
tool result with `isError: true` and the human-readable reason, so the model
can recover without the call having run.

## In front of another server

```bash
python3 -m exeat mcp \
  --policy agent-policy.yaml \
  --manifest tool-manifest.yaml \
  --agent clerk \
  -- python3 path/to/upstream_server.py
```

The upstream is a subprocess speaking newline-delimited JSON-RPC. Exeat
initializes it, caches `tools/list` for the life of the process, and forwards
a `tools/call` only after an allow. A denied call is not forwarded. Spend is
reserved before the forward and released if the upstream returns an error.

## HTTP upstream

`exeat mcp-http` can forward allowed calls to a remote MCP HTTP endpoint.
The client's `Authorization` header is copied onto the upstream request and
is not parsed, stored, or written to the audit log. Redirects are not
followed, so the bearer token is not sent to another host.

```bash
python3 -m exeat mcp-http \
  --policy agent-policy.yaml \
  --manifest tool-manifest.yaml \
  --agent clerk \
  --upstream https://mcp.example.test/mcp
```

The unofficial Encord Merlin guard uses this path. See [Encord Merlin](encord.md).
