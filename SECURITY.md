# Security policy

Exeat is a personal open-source project. It is a local gate in front of
agent tools. It is not a hosted service, and this repository contains no
customer data.

## Reporting a vulnerability

Once this repository is published, use GitHub private vulnerability reporting.
Until then, treat issues as public. Do not include live secrets. This project
ships none: the finance demo is synthetic, and the placeholder in
`examples/finance-assistant/secrets/` is not a credential.

## Threat model

Exeat assumes:

- The policy file and the tool manifest are written by someone you trust more
  than the agent. An agent that can edit its own policy can grant itself
  anything. Review policy changes with the permission diff, and keep the files
  out of the agent's writable scope.
- Tool arguments are structured. The gate does not read the prompt. A poisoned
  document can only cause harm by making the model emit a tool call, and that
  call is what gets allowed, denied, or held.
- The audit log and the approval file are local and append-only. Spend is a
  separate ledger: SQLite by default, shared by processes on one machine
  through atomic reservations, with optional Postgres when more than one
  machine must share it. The JSONL audit is not that ledger.

What a matching policy does **not** stop:

- Misuse of a tool the agent is allowed to call, inside the granted scope.
  A $180 payment to the wrong internal vendor is still allowed if it is under
  the ceiling and the recipient rules do not apply to vendor ids.
- A person who approves a call they did not read. Approvals are bound to the
  exact arguments, then consumed. They do not raise a ceiling.
- A manifest that lies about a non-reserved argument. Reserved names (`path`,
  `to`, `bcc`, `amount_usd`, and the rest in `exeat/brand.py`) stay
  sensitive even if the manifest downgrades them. Other names are trusted as
  classified.
- The protected-path list. It is a best-effort denylist for obvious secret
  locations (`.env`, `secrets/`, private keys, credential files). It is not a
  data-loss product, and it cannot be turned off from the policy file.

## Hardening notes for the local board

`exeat dashboard` binds to `127.0.0.1` by default. Do not expose it.
There is no authentication. The "try a call" form runs the same gate as the
agent and, for the finance demo, the simulated tools. Those tools do not open
sockets or send mail.

The project has no telemetry and does not call a model.
