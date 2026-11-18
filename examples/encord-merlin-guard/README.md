# Exeat Guard for Encord Merlin (unofficial)

Encord does not endorse this example. The policy, the mock server, and the tool names are a starter for putting Exeat in front of a Merlin-shaped MCP server. The live Merlin tool list was not inspected.

```bash
python3 -m exeat encord-demo
python3 -m exeat test examples/encord-merlin-guard/policy-tests.yaml
python3 -m exeat verify \
  --policy examples/encord-merlin-guard/agent-policy.yaml \
  --public examples/encord-merlin-guard/policy.pub
```

`encord-demo` starts the mock and the HTTP proxy, sends a bearer token through untouched, holds a prompt-injected self-admin grant, then verifies the hash-chained audit log. The private signing key is not in this repository.
