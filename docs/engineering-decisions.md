# Engineering decisions

Template. Each section describes what the code does today. It is not a design memoir.

**Why I chose this:** TODO (author). Do not invent a reason here.

**Open questions:** TODO (author).

## SQLite default ledger and its transaction guarantees vs Postgres

### Problem

A spend call has to be checked against per-call, per-day, and total ceilings, and two overlapping calls must not both commit an amount that only one of them fits under.

### Alternatives

TODO (author). The tree contains two ledger dialects, not a written comparison of rejected ones.

### Choice

`Leash.open` builds `Ledger.sqlite` beside the audit file (`src/exeat/runtime/leash.py`, `Ledger.sqlite` in `src/exeat/ledger.py`). `Ledger.open` also accepts a `sqlite:` URL or a `postgres://` / `postgresql://` URL. Postgres imports `psycopg` and raises `ExeatError` if that package is missing.

SQLite is opened with `isolation_level=None` (autocommit), `PRAGMA journal_mode=WAL`, and `PRAGMA busy_timeout=5000`. `try_reserve` holds an in-process `threading.Lock`, runs `BEGIN IMMEDIATE`, inserts a row into `agent_lock` with `ON CONFLICT DO NOTHING`, sums `reserved` and `committed` rows, and inserts a `reserved` row before `COMMIT`. On SQLite that `agent_lock` insert does not run `SELECT … FOR UPDATE`. On Postgres, `_lock_agent` does run `SELECT agent FROM agent_lock WHERE agent = ? FOR UPDATE` inside `BEGIN`.

`commit` and `release` are later `UPDATE`s, each under the same threading lock, not inside the `try_reserve` transaction. `release` moves a `reserved` or `committed` row to `released`. Sums ignore `released`.

**Why I chose this:** TODO (author).

### Tested how

`tests/test_production.py::test_ledger_reservations_are_atomic` opens two SQLite connections and asserts that two overlapping reservations of 60_000 cents against a 100_000 cent total produce one success and one failure. `tests/test_rest.py::test_postgres_dialect_reserves_and_rolls_back` exercises the Postgres SQL text with a fake connection, not a live server. `tests/test_runtime.py::test_daily_ceiling_and_release` covers release of a reservation after a tool error.

### Open questions

TODO (author).

## Approval binding to an action hash

### Problem

A hold has to be lifted only for the call a person approved.

### Alternatives

TODO (author).

### Choice

`fingerprint` in `src/exeat/canon.py` hashes the canonical JSON of `agent`, `tool`, and `arguments` with SHA-256 and keeps 20 hex characters. Amounts are normalized to two decimal places before hashing.

`ApprovalLog.grant` (`src/exeat/runtime/approvals.py`) appends a JSONL record with `type: approval`, that fingerprint, the agent, the tool, `by`, and `expires`. It does not store the argument object. Default TTL is 1800 seconds. `ttl_seconds <= 0` raises `ValueError`.

`_matching_grant` in `src/exeat/policy/evaluate.py` walks the approval file. It skips records whose `type` is not `approval` (missing `type` is treated as approval). It requires the same agent and the same fingerprint, an id that has no later `type: consumed` row, and `expires` strictly after `now`.

`Leash.run` calls `decide` first. On allow it reserves spend, appends an audit line, then `ApprovalLog.consume` appends `type: consumed` with that approval id. `consume` does not take a lock that covers the earlier read in `decide`. The JSONL append itself uses `fcntl.flock` for the duration of one write (`src/exeat/jsonl.py`).

A denial record (`ApprovalLog.deny`) matches in `_matching_denial` on agent, fingerprint, and unexpired `expires`, and `evaluate` returns `approval.denied` before the grant check.

**Why I chose this:** TODO (author).

### Tested how

`tests/test_runtime.py::test_approval_is_bound_to_the_arguments` grants one memo and runs a different memo, and runs the original memo with a clock past the default TTL. `tests/test_production.py::test_explicit_denial_blocks_a_later_allow` covers a denial record. `docs/experiments/approval-replay.md` is an unfilled experiment for replay, expiry, overlap, and a second agent. It does not record results.

### Open questions

TODO (author).

## Signed policies with Ed25519

### Problem

A policy file needs a way to detect that its bytes changed after signing.

### Alternatives

TODO (author).

### Choice

`sign_file` in `src/exeat/signing.py` signs the raw file bytes with an Ed25519 private key and writes base64 plus a newline to `policy-path.sig` (`signature_path`). `verify_file` reads those bytes back and calls `Ed25519PublicKey.verify`. `generate_keypair` writes PKCS8 PEM to `policy.key` (mode `0600`) and SubjectPublicKeyInfo PEM to `policy.pub`. The signature does not cover a parsed or normalized document. CLI: `exeat keygen`, `exeat sign`, `exeat verify`.

The finance example is not shipped with a signature. `examples/encord-merlin-guard/agent-policy.yaml.sig` and `policy.pub` are in the tree. The private key is not.

**Why I chose this:** TODO (author).

### Tested how

`tests/test_production.py::test_sign_and_verify_roundtrip` signs a file, verifies it, appends a newline, and expects verification to fail. `tests/test_gaps.py::test_signing_rejects_bad_inputs` covers missing and malformed inputs.

### Open questions

TODO (author).

## Deny-by-default

### Problem

A call that the policy does not grant has to fail closed.

### Alternatives

TODO (author).

### Choice

`evaluate` in `src/exeat/policy/evaluate.py` returns deny when the agent is unknown, the tool is absent from the manifest, the tool is not in the agent's effective grant, required arguments are missing, or a path, URL, table, or recipient is out of scope. Protected paths are applied in `src/exeat/policy/protected.py` and checked from `path_problem` even when an allow pattern would otherwise match. Spend with an unset ceiling is deny (`spend.no_ceiling`). Rate limits, when present, deny after the ceiling checks and before approval matching.

An agent document cannot add tools, allow-patterns, or a ceiling the role does not have (`_narrow` and `_tighten_spend` in `src/exeat/policy/effective.py`). Unknown policy keys are not rejected by the loader; it reads the keys it knows.

`exeat diff` (`src/exeat/diff/compare.py`) flags grant growth. `should_fail` defaults to failing at medium and above in the CLI.

**Why I chose this:** TODO (author).

### Tested how

`tests/test_evaluate.py` covers unknown agents, undeclared tools, protected paths, recipient scope, and ceilings. `tests/test_load.py` covers agents that try to raise a ceiling or add an allow. `tests/test_diff.py` covers the escalation and narrowing fixtures. `python3 -m exeat test` runs `policy-tests.yaml` for the finance, support, and research examples.

### Open questions

TODO (author).

## MCP proxy design

### Problem

A tool call from an MCP client has to be decided before it is forwarded.

### Alternatives

TODO (author). The tree has a stdio upstream and an HTTP upstream.

### Choice

Stdio: `exeat mcp -- <command>` builds `ProxyBackend` (`src/exeat/mcp/proxy.py`). The child is a subprocess. Its stdout is the JSON-RPC stream and is not inherited as the outer stdout. The proxy sends `initialize`, `notifications/initialized`, and `tools/list`, and keeps the tool names for the process lifetime. `Session._call` in `src/exeat/mcp/server.py` runs `Leash.run`, and `Leash.run` calls the backend only when the effect is allow. A deny or hold is returned as a tool result with `isError: true` and is not forwarded. `tools/list` advertises the intersection of the grant and `backend.supports`.

HTTP: `exeat mcp-http` serves `GET /sse` and `POST /messages?session=` (`src/exeat/mcp/http.py`). With `--upstream` or `--region`, each session gets an `HttpUpstream` (`src/exeat/mcp/upstream_http.py`). `set_authorization` stores the raw `Authorization` header value. The upstream POST copies that value and does not log it. `UrllibTransport` uses an opener whose redirect handler returns `None`. `--region eu` is `https://mcp.encord.com/mcp` and `--region us` is `https://mcp.us.encord.com/mcp` (`src/exeat/integrations/encord/endpoints.py`). The Merlin demo points the upstream at a local mock (`src/exeat/integrations/encord/mock.py`), not at Encord.

**Why I chose this:** TODO (author).

### Tested how

`tests/test_mcp.py::test_proxy_does_not_forward_a_denied_call` checks that a denied tool is absent from the upstream log and an allowed tool is present. `tests/test_encord.py` checks bearer forwarding, hold-not-forwarded, and that redirects are not followed by `_NoRedirect`. `tests/test_production.py::test_http_mcp_roundtrip` covers the SSE gate with the builtin finance backend.

### Open questions

TODO (author).

## Hash-chained audit log

### Problem

The decision tape has to show whether a line was changed, removed, or reordered after it was written.

### Alternatives

TODO (author).

### Choice

`AuditLog._append` in `src/exeat/runtime/audit.py` locks the file with `fcntl.flock`, reads the previous line's `hash`, and writes one JSON object. The object includes `prev` and `hash`. `hash` is SHA-256 of the canonical JSON of every field except `hash` (`chain_hash`). The first line uses `prev` of 64 zero characters (`GENESIS`). The lock covers that read and the following write.

`verify_chain` recomputes each hash and checks `prev` against the prior line. A line without `prev` or `hash` is a failure. An empty file verifies. `export_audit` writes the records and a `verified` boolean. `_tail_hash` refuses to append if the last line is not JSON or has no `hash`.

`record` and `release` both go through `_append`. Approvals are a separate JSONL file and are not part of this chain.

**Why I chose this:** TODO (author).

### Tested how

`tests/test_encord.py::test_audit_chain_detects_tampering_and_exports` checks a fresh chain, a changed field, a missing hash, a broken JSON tail, and export of an empty file. `exeat audit verify` and `exeat audit export` call `verify_chain` and `export_audit` from `src/exeat/cli.py`.

### Open questions

TODO (author).
