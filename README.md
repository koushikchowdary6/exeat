# Exeat

**The permission slip for AI agents.**

Runtime authorization and permission-change detection for AI agents.

[![CI](https://github.com/koushikchowdary6/exeat/actions/workflows/ci.yml/badge.svg)](https://github.com/koushikchowdary6/exeat/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

## The problem

Instructions in a prompt are not an authorization boundary. The model can be talked into requesting a tool call the operator did not intend. The check has to happen on the tool call, in a process the model does not control, before the tool runs.

## What it does

Three capabilities:

1. **Runtime allow, deny, or hold.** `evaluate` in `src/exeat/policy/evaluate.py` decides one call. Deny and hold do not run the tool. A hold can be lifted only by an approval whose fingerprint matches that call (`src/exeat/canon.py`, `src/exeat/runtime/approvals.py`).
2. **Permission-change detection in CI.** `exeat diff` compares two policies after inheritance and exits non-zero when a grant grows. The workflow is `.github/workflows/permission-diff.yml`.
3. **Auditability.** Each decision is appended to a hash-chained JSONL tape (`src/exeat/runtime/audit.py`). `exeat audit verify` recomputes the chain.

## Design questions

The implementation is aimed at four questions. The code, not this paragraph, is the answer.

- Can the decision ignore the model output and the prompt, and look only at the tool name and arguments?
- Do spend ceilings still hold when two calls are in flight? The reservation path is `Ledger.try_reserve` in `src/exeat/ledger.py`.
- Is an approval bound to one exact action? The fingerprint is a truncated SHA-256 of agent, tool, and canonical arguments. Replay is an open experiment: [docs/experiments/approval-replay.md](docs/experiments/approval-replay.md).
- Does CI notice when a policy edit gives an agent something it did not have?

Notes on those mechanisms are in [docs/engineering-decisions.md](docs/engineering-decisions.md). Several "why" sections there are still empty on purpose.

## Current limitations

- Early-stage. The public API is the CLI and `Leash`. It will move.
- Not independently audited.
- The OpenAI Agents SDK, LangChain, and CrewAI wrappers are tested with stand-ins. They do not import those packages unless you installed them.
- The Encord integration is unofficial. Encord does not endorse it. The demo talks to a mock Merlin, not to `mcp.encord.com`.

This repository is built with AI coding agents under the author's direction.

## Flagship demo

One agent, one $900 transfer, three outcomes. The role holds transfers above $700, allows $1,000 per call, and allows $2,500 per day. The command commits a $180 payment and a $600 payment first, so the last $900 crosses the daily ceiling. Nothing is sent to a bank. Northwind Books is fictional.

```bash
python3 -m pip install -e .
python3 -m exeat demo --payment
```

Captured from one run of that command:

```
exeat demo  ·  finance-assistant  ·  Northwind Books
deny by default  ·  builtin tools  ·  no network

Same $900 transfer, three times. The role holds amounts above $700, allows $1,000 per call, and allows $2,500 per day. $180 and $600 are committed first so the last $900 crosses the daily ceiling.

 1  ALLOW  finance-assistant     ledger.transfer
         $180 to VEND-OAK
         Allowed: ledger.transfer is granted to finance-assistant. $180.00 is within the per-call ceiling of $1,000.00. Today $180.00 of $2,500.00. Total $180.00 of $10,000.00.
 2  ALLOW  finance-assistant     ledger.transfer
         $600 to VEND-INK
         Allowed: ledger.transfer is granted to finance-assistant. $600.00 is within the per-call ceiling of $1,000.00. Today $780.00 of $2,500.00. Total $780.00 of $10,000.00.
 3  HOLD   finance-assistant     ledger.transfer
         $900 to VEND-OAK
         Needs a person: $900.00 on ledger.transfer. Transfers above $700 need a person. (large-transfer: amount_usd > $700.00 on ledger.transfer).
 4  ALLOW  finance-assistant     ledger.transfer
         $900 to VEND-OAK
         Allowed: ledger.transfer is granted to finance-assistant. $900.00 is within the per-call ceiling of $1,000.00. Today $1,680.00 of $2,500.00. Total $1,680.00 of $10,000.00. Approval apr_72727bbe9087 by demo-controller covers this call.
 5  DENY   finance-assistant     ledger.transfer
         $900 to VEND-OAK
         Denied: $900.00 would bring today's spend to $2,580.00, over the daily ceiling of $2,500.00.

5 calls  ·  3 allowed  ·  1 held  ·  1 denied
audit      /tmp/exeat-payment-audit.jsonl
dashboard  python -m exeat dashboard
```

The approval id (`apr_72727bbe9087` in this capture) is random on each run. The effects, rules, and dollar amounts are not. Re-run the command and compare those.

`exeat check` does not write the audit log. Exit 0 is allow, 1 is deny, 3 is hold, 2 is a bad policy or bad usage.

## Install

Python 3.11 or newer.

```bash
git clone https://github.com/koushikchowdary6/exeat.git
cd exeat
python3 -m pip install -e .
python3 -m exeat demo
python3 -m exeat dashboard
```

`exeat demo` is the longer offline script (20 calls). The board is [http://127.0.0.1:4747](http://127.0.0.1:4747), loopback only, with no account and no model key.

![The local permission board: roles, two agents with spend meters, and a held payment](docs/images/board.png)

## Also in the tree

- Deny-by-default policy: roles, agents, tools, spend ceilings, recipients, file, URL, and table scopes, approval rules, and optional per-minute rate limits. An agent can only narrow a role. Protected paths such as `secrets/` and `.env` cannot be granted. Reference: [docs/policy.md](docs/policy.md).
- A Python decorator, MCP on stdio, and MCP over HTTP/SSE. Optional wrappers for the OpenAI Agents SDK, LangChain/LangGraph, and CrewAI.
- SQLite ledger by default. Postgres is an optional extra (`psycopg`) for a shared ledger. The demo does not start Postgres.
- `exeat approve` and `exeat deny`, or the same actions on the board. An optional webhook is notified on hold. A failed POST does not change the decision.
- Ed25519 signatures over the policy file bytes: `exeat keygen`, `exeat sign`, `exeat verify`.
- Examples: `examples/finance-assistant`, `examples/support-inbox`, `examples/research-assistant`. Each has `policy-tests.yaml`.
- Unofficial Encord Merlin guard: `examples/encord-merlin-guard` and `python3 -m exeat encord-demo`. Mock upstream. See [docs/encord.md](docs/encord.md).

```bash
python3 -m exeat test examples/finance-assistant/policy-tests.yaml
python3 -m exeat test examples/support-inbox/policy-tests.yaml
python3 -m exeat test examples/research-assistant/policy-tests.yaml
python3 -m exeat test examples/encord-merlin-guard/policy-tests.yaml
```

Docs site: `python3 -m mkdocs serve`.

## Permission diff

`examples/finance-assistant/fixtures/escalated-policy.yaml` is a widened edit of the demo policy: a shell tool, higher ceilings, another recipient domain, a looser hold, and a manifest that labels `path` as a plain string.

```bash
python3 -m exeat diff --example escalation
```

The command exits 1. High means an existing grant grew or a check was removed. Medium means a new principal or a new single address. Both fail by default. `--fail-on high` still prints the medium findings and does not fail on them. A tightening exits 0:

```bash
python3 -m exeat diff --example narrowing
```

Wire it with [`.github/workflows/permission-diff.yml`](.github/workflows/permission-diff.yml) and [`.github/actions/exeat-diff`](.github/actions/exeat-diff/action.yml). The action comments a markdown table and writes SARIF. From another repository:

```yaml
- uses: koushikchowdary6/exeat/.github/actions/exeat-diff@main
```

That action is not published as its own repository. The copy in this tree is what CI runs.

Between git refs:

```bash
python3 -m exeat diff \
  --rev-base "$BASE_SHA" \
  --rev-head "$HEAD_SHA" \
  --policy agent-policy.yaml \
  --manifest tool-manifest.yaml
```

`.pre-commit-config.yaml` runs `python -m exeat hook` on changed policy files.

## From Python

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
    return {"vendor_id": vendor_id, "amount_usd": amount_usd}

transfer(amount_usd=180, vendor_id="VEND-OAK")
```

`Denied` and `ApprovalRequired` are raised before the function runs. `exeat approve` records a grant for that agent, tool, and argument fingerprint. It expires. `Leash.run` appends a `consumed` record when an allow that carries the approval id is audited.

```bash
python3 -m exeat mcp          # stdio
python3 -m exeat mcp-http     # HTTP and server-sent events, port 4748
```

One agent per MCP process. The board can try any agent in the file because it is an operator console.

## Decision order

Deny by default. `evaluate` checks: unknown agent, undeclared tool, tool not granted, arguments, path and URL and table and recipient scope, spend ceilings, rate limits, then approval. Protected paths lose even to an allow of `**`. The policy cannot turn that list off.

| Effect | `exeat check` exit |
| --- | --- |
| allow | 0 |
| deny | 1 |
| require_approval | 3 |
| bad policy or bad usage | 2 |

`check` does not write the audit log. The decorator, MCP, the demo, and the board do.

```bash
python3 -m exeat explain \
  --agent finance-assistant \
  --tool files.read \
  --args '{"path":"secrets/api-keys.txt"}'
```

## What it does not do

- The condition language is `always` and one numeric threshold. It is not Rego or Cedar.
- An allowed tool can still be misused inside its scope.
- Glob comparison in the diff is conservative. An unusual pattern change can be flagged as a widening when it is not.
- The protected-path list is a denylist of obvious places, not data-loss prevention.
- Nothing is published to PyPI. The `Private :: Do Not Upload` classifier is set on purpose.

A comparison with OPA, Cedar, promptfoo, and garak is in [docs/comparisons.md](docs/comparisons.md). Those tools answer different questions. This one does not replace them.

## Project layout

```
src/exeat/                         gate, diff, MCP, board
examples/finance-assistant/        policy, manifest, synthetic books, bad fixtures
examples/support-inbox/            refunds that need a person
examples/research-assistant/       notes and a docs host, no spend
examples/encord-merlin-guard/      unofficial Merlin policy and mock
docs/engineering-decisions.md      decision template, mostly unfilled
docs/experiments/                  approval-replay experiment, results blank
scripts/approval_replay.py         runs that experiment and prints outcomes
.github/actions/exeat-diff/        composite action
.github/workflows/                 CI, permission diff, and a draft release
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Tests, ruff, and mypy run in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) on Python 3.11, 3.12, and 3.13. Coverage fails under 90%.

The longer list is in [docs/roadmap.md](docs/roadmap.md).

## Security

See [SECURITY.md](SECURITY.md). The policy is trusted more than the agent. The gate sees tool calls rather than prompts. The local board has no authentication because it binds to loopback.

## License

MIT License. Copyright (c) 2026 Sai Pranay Kowshik Chowdary Sakhamuri. See [LICENSE](LICENSE).

Created by Sai Pranay Kowshik Chowdary Sakhamuri ([@koushikchowdary6](https://github.com/koushikchowdary6)).
