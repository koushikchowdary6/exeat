# Integrations

## Python

```python
from exeat import Leash

leash = Leash.open(
    policy_path="agent-policy.yaml",
    manifest_path="tool-manifest.yaml",
    agent="finance-assistant",
    audit_path=".exeat/audit.jsonl",
    approval_path=".exeat/approvals.jsonl",
)

@leash.guard("ledger.transfer")
def transfer(amount_usd: float, vendor_id: str, memo: str = "") -> dict:
    return {"ok": True}
```

`Denied` and `ApprovalRequired` are raised before the function runs.

## MCP stdio

```bash
python3 -m exeat mcp --example --agent finance-assistant
```

Put `-- python server.py` after the flags to proxy another stdio server. A denied call is not forwarded.

## MCP over HTTP and SSE

```bash
python3 -m exeat mcp-http --example --port 4748
```

`GET /sse` opens the stream. The first event is the POST endpoint for JSON-RPC messages. Responses come back on the stream.

## OpenAI Agents SDK, LangChain, CrewAI

The adapters live in `exeat.adapters`. They wrap the function with the gate first. If the framework package is installed they hand the guarded function to that package. Tests pass a fake factory and do not import the frameworks.

```python
from exeat.adapters import as_crew_tool, as_tool, function_tool
```

`function_tool(leash, "files.read")` is the OpenAI Agents SDK shape. `as_tool` is the LangChain and LangGraph shape. `as_crew_tool` builds a CrewAI tool.

## Approval webhook

`Leash.open(..., webhook_url="http://127.0.0.1:9/hooks/hold")` POSTs JSON when a call is held. If `webhook_url` is omitted, nothing is sent. A failed POST does not change the decision.

## GitHub Action and pre-commit

The composite action is `.github/actions/exeat-diff`. It comments a markdown table and writes `permission-diff.sarif`.

`.pre-commit-config.yaml` runs `python -m exeat hook`, which diffs changed policy files against `HEAD`.
