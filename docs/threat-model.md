# Threat model

Exeat assumes the policy and the manifest are written by someone you trust more than the agent. An agent that can edit its own policy can grant itself anything. Review those files with the permission diff, and keep them out of the agent's writable paths.

The gate sees structured tool arguments. It does not read the prompt. A poisoned document can only cause harm by making the model emit a tool call, and that call is what is allowed, denied, or held.

## What a matching policy does not stop

- Misuse of an allowed tool inside its scope. A payment to the wrong internal vendor is still allowed when the amount and the recipient rules pass.
- An approver who does not read the arguments. Approvals are bound to the exact arguments and then consumed. They do not raise a ceiling.
- A manifest that lies about a non-reserved argument. Reserved names such as `path`, `to`, `bcc`, and `amount_usd` stay sensitive even if the manifest downgrades them.
- The protected-path list. It blocks obvious secret locations (`.env`, `secrets/`, private keys, credential files). It is not a data-loss product, and the policy cannot turn it off.

## Local board

`exeat dashboard` binds to `127.0.0.1`. It has no authentication. Do not expose it. The finance tools behind the board do not open sockets or send mail.

## Ledger

SQLite with a write-ahead log and an immediate transaction is the default. It serializes writers on one machine. Postgres is the option when several machines must share one ledger. The audit JSONL is a tape, not the balance.

There is no telemetry.
