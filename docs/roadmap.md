# Roadmap

The name is not final. It lives in `src/agentleash/brand.py` and the package name. Publishing to PyPI is intentionally blocked.

## Good first issues

1. **YAML line numbers on validation errors.** `PolicyError` carries a message and a code. Pointing at the line in the file would make `validate` easier to read.
2. **A fourth example agent.** Add a synthetic calendar or deploy agent with a policy, a manifest, and `policy-tests.yaml`. Follow `examples/support-inbox`.
3. **Show the signature status on the board.** `verify` already checks a `.sig` file. Surface "signed" or "unsigned" next to the policy hash.
4. **Postgres concurrency test.** The SQL dialect is in `ledger.py`. A test against a local Postgres, skipped when no server is configured, would lock the `FOR UPDATE` path.
5. **Pre-commit documentation pass.** `.pre-commit-config.yaml` runs `agentleash hook`. Add a short troubleshooting note to the contributing guide if a new policy file should fail closed.
6. **Richer approval conditions.** The current rules are `always` and `amount > threshold`. A second comparison, still deny-by-default, would cover "memo matches" without becoming a general policy language.

## Later

- Signed commits of the policy, not only a detached signature.
- An approval inbox that is still a local file, but easier to clear than JSONL.
- MCP HTTP beyond the SSE transport.
