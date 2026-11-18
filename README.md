# Agentleash

An org chart for what an AI agent is allowed to spend, send, and open.

Agentleash sits on the tool call. It allows the call, denies it, or holds it
for a person, and it prints the reason in a sentence. The same policy is
diffed in CI, so a reviewer sees what an agent newly gains before it ships.

![The local permission board: roles, two agents with spend meters, and the audit tape](docs/images/board.png)

<video src="docs/images/demo.mp4" controls></video>

Northwind Books is fictional. The demo does not spend money, send mail, or
use the network.

## 30-second start

Python 3.11+. From a fresh clone:

```bash
python3 -m pip install -e .
python3 -m agentleash demo
```

Twenty scripted calls. Eight are allowed, one waits for a person, eleven are
denied. The audit log is `.agentleash/audit.jsonl` next to the example. Then:

```bash
python3 -m agentleash dashboard
```

Open [http://127.0.0.1:4747](http://127.0.0.1:4747). The board is loopback
only. There is no account, no telemetry, and no model key.

## Why this exists

General policy engines and agent scanners answer different questions.

| | Agentleash | OPA, Cedar | promptfoo, garak |
| --- | --- | --- | --- |
| Question | What may this agent spend, send, and open, and what just changed? | Does this request satisfy a policy you wrote? | Does this model fail a safety eval? |
| When it runs | On the tool call, and again as a diff on the pull request | Wherever you evaluate the policy | In an eval or a red-team run |
| Spend, recipients, paths | First-class, deny by default | Only if you model them | No |
| Sees permission drift before merge | Yes. That is the point. | You would build the diff | No |
| Tests whether the model obeys the prompt | No | No | Yes |

OPA and Cedar are real languages. They can express an agent policy if you
write the model of tools, money, and paths yourself. They will not tell a
reviewer that `finance-assistant` just gained `shell.exec` and a $5,000
ceiling. promptfoo and garak are the right tools for "does the model follow
the injection." They do not sit between the model and the tool, and they do
not fail a pull request because a ceiling went up.

Agentleash is the narrow piece in the middle. It does not replace either of
them.

## What you get

```
policy.yaml ──┐
manifest.yaml ┼──► leash ── allow / deny / hold ──► tool
audit.jsonl ◄─┘              │
                             └── CI diff on the pull request
```

1. **A policy file.** Roles, agents, tools, spend ceilings, recipients,
   file and URL and table scopes, and rules that need a person. Agents can
   only narrow a role. See [docs/policy.md](docs/policy.md).
2. **A runtime gate.** A Python decorator, and an MCP server that can serve
   the builtin finance tools or proxy another stdio server. Decisions are
   appended to a JSONL log. Spend is reserved before the tool runs and
   released if the tool raises.
3. **A permission diff.** `agentleash diff` compares two revisions and exits
   non-zero when an agent gains authority. A GitHub Action posts the diff
   on the pull request.
4. **A local board and a terminal chart.** The org chart, the ceilings, and
   the live tape. `agentleash chart` prints the same tree without a browser.

## The finance demo

`finance-assistant` is a scripted accounts-payable clerk. `finance-intern`
has the same role and a $50 ceiling. The invoice memo is poisoned. The gate
never reads it. It sees the tool call the injection asked for, and that tool
is not granted.

```
 5  ALLOW  finance-assistant     ledger.transfer
         $180 to VEND-OAK
 8  HOLD   finance-assistant     ledger.transfer
         $900 to VEND-OAK
         Needs a person: Transfers above $700 need a person.
 9  ALLOW  finance-assistant     ledger.transfer
         $900 to VEND-OAK
         Approval covers this call. Today $1,680.00 of $2,500.00.
10  DENY   finance-assistant     ledger.transfer
         $900.00 would bring today's spend to $2,580.00, over $2,500.00.
13  DENY   finance-assistant     files.read
         secrets/api-keys.txt matches protected pattern **/secrets/**
18  DENY   finance-assistant     shell.exec
         tool shell.exec is not declared, so the call was not forwarded.
19  DENY   finance-assistant     email.send
         bcc attacker@evil.test is outside the allowed domains.
20  DENY   finance-intern        ledger.transfer
         $180.00 exceeds the per-call ceiling of $50.00.
```

A hold does not raise a ceiling. The second $900 is denied on the daily
limit even though it would also have needed a person. `bcc` is not in the
manifest. The name is reserved, so it is still checked.

The same calls are on the board under **Try a call**, including
[the secrets denial](http://127.0.0.1:4747/?preset=read-secrets&run=1)
while the server is running.

![A denied read of secrets/api-keys.txt, stamped Deny](docs/images/deny.png)

## The CI diff

This is the part to show a reviewer. The fixture
`examples/finance-assistant/fixtures/escalated-policy.yaml` is a bad edit of
the demo policy: a shell tool, higher ceilings, `evil.test`, a looser hold,
and a manifest that pretends `path` is a plain string.

```bash
python3 -m agentleash diff --example escalation
```

```
HIGH  agent finance-intern
      Raised the per-call ceiling from $50.00 to $800.00.
HIGH  manifest
      files.read argument path changed from path to string.
      Reserved names are still enforced at runtime, but the manifest
      no longer tells reviewers the truth.
HIGH  role finance-analyst
      Approval large-transfer threshold rose from $700.00 to $5,000.00.
HIGH  role finance-analyst
      Gained tool shell.exec. Now held by finance-assistant,
      finance-intern, payroll-bot.
HIGH  role finance-analyst
      New recipient domain evil.test.
MED   agent payroll-bot
      New agent payroll-bot on role finance-analyst.
NOTE  role finance-reader
      Narrowed table allow by dropping finance.vendors.

11 high, 2 medium, 1 low, 2 note. Risky escalation: failing CI at medium and above.
```

The command exits 1. A tightening of the same policy exits 0:

```bash
python3 -m agentleash diff --example narrowing
```

High means an existing grant grew or a check was removed. Medium means a new
principal or a new single address. Both fail by default. `--fail-on high`
keeps the new-agent warning and does not fail the job on it.

Wire it to pull requests with
[`.github/workflows/permission-diff.yml`](.github/workflows/permission-diff.yml)
and the composite action in
[`.github/actions/agentleash-diff`](.github/actions/agentleash-diff/action.yml).
The action comments on the pull request and fails the job. Copy the workflow
and point `policy` and `manifest` at your files.

Between git refs, from a checkout:

```bash
python3 -m agentleash diff \
  --rev-base "$BASE_SHA" \
  --rev-head "$HEAD_SHA" \
  --policy agent-policy.yaml \
  --manifest tool-manifest.yaml
```

## Use it from Python

```python
from agentleash import Leash

leash = Leash.open(
    policy_path="agent-policy.yaml",
    manifest_path="tool-manifest.yaml",
    agent="finance-assistant",
    audit_path=".agentleash/audit.jsonl",
    approval_path=".agentleash/approvals.jsonl",
)

@leash.guard("ledger.transfer")
def transfer(amount_usd: float, vendor_id: str, memo: str = "") -> dict:
    return {"vendor_id": vendor_id, "amount_usd": amount_usd}

transfer(amount_usd=180, vendor_id="VEND-OAK")
```

A denial raises `Denied`. A hold raises `ApprovalRequired`. Neither runs the
function. `agentleash approve` records a grant bound to those exact
arguments. It expires, and it is consumed when the call is reserved.

One agent per MCP process. The board can try any agent in the file because
it is an operator console, not the agent.

## Decisions

Deny by default. The order is: unknown agent, undeclared tool, tool not
granted, bad arguments, path and URL and table and recipient scope, spend
ceilings, then approval. Protected paths (`.env`, `secrets/`, private keys,
credential files) lose even to an allow of `**`, and the policy cannot turn
that list off.

| Effect | `agentleash check` exit |
| --- | --- |
| allow | 0 |
| deny | 1 |
| require_approval | 3 |
| bad policy or bad usage | 2 |

`check` does not write the audit log. The decorator, MCP, the demo, and the
board do.

## Limits

- One local audit file. A file lock covers processes on one machine. It is
  not a shared ledger, and a crash after a tool succeeds but before the
  process exits can leave a reservation that a human has to read.
- The condition language is the small one in the policy file. It is not
  Rego or Cedar.
- MCP is newline-delimited JSON-RPC on stdio.
- An allowed tool can still be misused inside its scope.
- Glob comparison in the diff is conservative. An unusual pattern change can
  be flagged as a widening when it is not. The other direction, hiding a
  real widening, is the one this tries to avoid.
- The protected-path list is a denylist of obvious places, not data-loss
  prevention.
- The working name has not been checked for trademark or package conflicts.
  Nothing is published to PyPI. User-facing strings live in
  [`src/agentleash/brand.py`](src/agentleash/brand.py). Renaming also means
  the package name in `pyproject.toml` and the `src/agentleash` directory.
  The `Private :: Do Not Upload` classifier is set on purpose.

## Roadmap

- A spend ledger that more than one machine can share.
- Signed policy files, so a review is of a specific bytestring.
- MCP over HTTP.
- Conditions past the current threshold and "always" rules.
- An approval inbox that is still local, but easier to clear than a JSONL file.
- A decision on the name, and only then a package publish.

## Project layout

```
src/agentleash/          gate, diff, MCP, board
examples/finance-assistant/   policy, manifest, synthetic books, bad fixtures
docs/                    policy reference and MCP notes
.github/actions/agentleash-diff/   composite action
.github/workflows/       CI, and the pull-request permission diff
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Tests, ruff, and mypy run in
[`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Security

See [SECURITY.md](SECURITY.md). The short version: the policy is trusted more
than the agent, the gate sees tool calls rather than prompts, and the local
board has no authentication because it binds to loopback.

## Operate it

```bash
python3 -m agentleash validate --example
python3 -m agentleash test examples/finance-assistant/policy-tests.yaml
python3 -m agentleash explain --agent finance-assistant --tool files.read --args '{"path":"secrets/api-keys.txt"}'
python3 -m agentleash keygen --out .agentleash/keys
python3 -m agentleash sign --policy examples/finance-assistant/agent-policy.yaml --key .agentleash/keys/policy.key
python3 -m agentleash verify --policy examples/finance-assistant/agent-policy.yaml --public .agentleash/keys/policy.pub
```

Spend reservations live in SQLite (`ledger.sqlite` next to the audit log) and are atomic across processes on one machine. Set a `postgres://` URL via `Ledger.open` when more than one machine must share the ledger. Postgres needs the optional `postgres` extra. Nothing is uploaded anywhere.

`agentleash deny` records an operator rejection of one exact call. The board has the same Deny button next to Approve. Set `--webhook` on the board, or `webhook_url` on `Leash.open`, to POST a hold to a local endpoint. If the URL is unset, no request is made.

`agentleash mcp-http` speaks MCP over HTTP and server-sent events. Adapters for the OpenAI Agents SDK, LangChain, and CrewAI are in `agentleash.adapters` and do not import those packages unless they are installed.

The product title and command slug live in [`src/agentleash/brand.py`](src/agentleash/brand.py). The import package uses the same slug. The name is not final. This project is not published to PyPI (`Private :: Do Not Upload`).

## License

MIT License. Copyright (c) 2026 Sai Pranay Kowshik Chowdary Sakhamuri. See [LICENSE](LICENSE).
