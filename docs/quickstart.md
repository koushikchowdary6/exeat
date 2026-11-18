# Quick start

Python 3.11 or newer.

```bash
python3 -m pip install -e .
python3 -m exeat demo
python3 -m exeat dashboard
```

The board listens on `http://127.0.0.1:4747`.

Check one call without writing the audit log:

```bash
python3 -m exeat explain \
  --agent finance-assistant \
  --tool files.read \
  --args '{"path":"secrets/api-keys.txt"}'
```

Run the declared policy cases:

```bash
python3 -m exeat test examples/finance-assistant/policy-tests.yaml
python3 -m exeat test examples/support-inbox/policy-tests.yaml
python3 -m exeat test examples/research-assistant/policy-tests.yaml
```

Sign a policy with a local Ed25519 key:

```bash
python3 -m exeat keygen --out .exeat/keys
python3 -m exeat sign --policy examples/finance-assistant/agent-policy.yaml --key .exeat/keys/policy.key
python3 -m exeat verify --policy examples/finance-assistant/agent-policy.yaml --public .exeat/keys/policy.pub
```

The signature covers the file bytes. Changing a space breaks it.
