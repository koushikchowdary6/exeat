# Concepts

## Deny by default

A tool call is denied unless the agent's role grants the tool and every sensitive argument is inside scope. Spend needs explicit ceilings. An unset ceiling is not unlimited.

## Roles and agents

A child role adds grants. It cannot delete a parent deny. An agent can only narrow its role: a lower ceiling, `exclude_tools`, or extra deny patterns. Raising a ceiling on the agent is a load error.

## Decisions

| Effect | Meaning |
| --- | --- |
| allow | The tool may run. |
| deny | The tool must not run. |
| require_approval | A person must approve these exact arguments. |

A hold does not raise a ceiling. An explicit denial of those arguments blocks the call until it expires.

## Spend ledger

Reservations are stored in SQLite next to the audit log. The insert happens inside a transaction after the ceilings are read, so two processes cannot both pass a limit that only one of them fits under. A failed tool releases the reservation. Committed rows keep counting.

Postgres is optional. Pass a `postgres://` URL to `Ledger.open` and install the `postgres` extra. The default install does not contact a database server.

## Audit tape

Every decision is appended to `audit.jsonl`. The file is not rewritten. The ledger, not the tape, is the source of spend totals.

## Signatures

`keygen`, `sign`, and `verify` use Ed25519. Verification needs the public key only. The demo does not require a signature, so it still runs offline on a fresh clone.
