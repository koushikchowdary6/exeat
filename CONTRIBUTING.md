# Contributing

Agentleash is a personal project under the MIT License. Issues and
pull requests are welcome. There is no company, no customers, and no private
tracker behind this repository.

## Setup

Python 3.11 or newer.

```bash
python3 -m pip install -e ".[dev]"
python3 -m ruff check src tests
python3 -m ruff format --check src tests
python3 -m mypy src
python3 -m pytest --cov=agentleash --cov-fail-under=90
python3 -m agentleash demo
```

The demo and the tests are offline. Do not add a dependency that phones home,
and do not add a required API key.

## What a change should include

- A deny-by-default test. If you add a grant, show the case that stays denied.
- A diff test if the change can widen what an agent can do. New authority
  should be `high` or `medium`, and `medium` fails CI by default.
- Type hints on new functions. `mypy --strict` runs in CI.

## Policy and manifest changes

The finance example is the contract the demo and a lot of the tests lock.
If you change a ceiling or a tool name, update `src/agentleash/demo/scenario.py`
in the same commit. The demo exits non-zero when a step does not match.

Reserved argument names live in `src/agentleash/brand.py`. Adding one is a
behavior change: calls that used that name as a plain string start getting
checked. Mention it in the pull request.

## The name

The working name has not been checked for trademark or package conflicts, and
nothing here is published to PyPI. User-facing strings come from
`src/agentleash/brand.py`. Renaming also means the `[project].name` field in
`pyproject.toml` and the `src/agentleash` directory.

## Commit style

Short subject, imperative mood, and a body if the reason is not obvious from
the diff. The permission diff is the review surface for policy changes; the
commit message should say which agent gained what.
