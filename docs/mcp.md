# MCP gate

Agentleash can sit on stdio in front of tools. The agent process talks MCP to
Agentleash. Agentleash decides, then either answers with the denial or
forwards the call.

No model is required. Point any MCP client at the process, including one that
runs a local model. Agentleash never calls a model and never reads a prompt.

## Builtin finance tools

```bash
python3 -m agentleash mcp --example --agent finance-assistant
```

This serves the simulated ledger, files, and mail from the finance example.
`http.get` returns canned data. Nothing opens a socket.

A client config, with the command adjusted to your checkout:

```json
{
  "mcpServers": {
    "finance": {
      "command": "python3",
      "args": ["-m", "agentleash", "mcp", "--example", "--agent", "finance-assistant"]
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
python3 -m agentleash mcp \
  --policy agent-policy.yaml \
  --manifest tool-manifest.yaml \
  --agent clerk \
  -- python3 path/to/upstream_server.py
```

The upstream is a subprocess speaking newline-delimited JSON-RPC. Agentleash
initializes it, caches `tools/list` for the life of the process, and forwards
a `tools/call` only after an allow. A denied call is not forwarded. Spend is
reserved before the forward and released if the upstream returns an error.

Stdio only, for now. HTTP transport is on the roadmap.
