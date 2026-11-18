# Experiment: can an approval be replayed?

An approval is recorded for one agent, one tool, and one argument fingerprint, with an expiry. This experiment asks whether that record can be used again. It does not answer the question.

## Hypothesis

A recorded approval covers one exact call, for the agent it names, until it expires, and a second overlapping use of that same record does not also pass.

## Setup

Python 3.11 or newer, from a checkout of this repository:

```bash
python3 -m pip install -e .
```

The script uses `examples/finance-assistant/agent-policy.yaml` and `tool-manifest.yaml`. It writes a fresh audit log, approval log, and SQLite ledger under a temporary directory for each case, then deletes them. It does not edit the example files.

Facts already in that policy, so the commands below are interpretable:

- `finance-assistant` is on role `finance-analyst`.
- `ledger.transfer` is held when `amount_usd` is above $700 (`large-transfer` in the policy).
- That role allows $1,000 per call and $2,500 per day.
- `finance-intern` is on the same role with a $50 per-call ceiling.

The call under test is:

```json
{"amount_usd": 900, "vendor_id": "VEND-OAK", "memo": "Oak Paper Co. rush reprint"}
```

`scripts/approval_replay.py` grants an approval, performs the replay described for that case, and prints `effect`, `rule`, `reason`, and how many `consumed` records name that approval id. It does not decide whether the printout is acceptable.

## Cases

Run one case, or all four:

```bash
python3 scripts/approval_replay.py --case different-arguments
python3 scripts/approval_replay.py --case expired
python3 scripts/approval_replay.py --case concurrent
python3 scripts/approval_replay.py --case different-agent
python3 scripts/approval_replay.py
```

### Different arguments

Grant the $900 call above. Replay it with `memo` set to `a different memo` and every other field unchanged.

### After expiry

Grant the same call with a 1-second TTL. Wait 2 seconds. Replay the same arguments.

### Concurrent reuse

Grant the same call once. Start two `finance-assistant` calls with those arguments, overlapping, against the same approval file and ledger.

### Different agent

Grant the call for `finance-assistant`. Replay the same arguments as `finance-intern`.

The same steps through the CLI, for the two cases that do not need a second thread. Replace the temporary paths with a directory you create. `exeat check` reads approvals and does not consume them. The script calls `Leash.run`, which can consume.

```bash
mkdir -p /tmp/exeat-replay
python3 -m exeat approve \
  --policy examples/finance-assistant/agent-policy.yaml \
  --manifest examples/finance-assistant/tool-manifest.yaml \
  --approvals /tmp/exeat-replay/approvals.jsonl \
  --audit /tmp/exeat-replay/audit.jsonl \
  --agent finance-assistant \
  --tool ledger.transfer \
  --args '{"amount_usd":900,"vendor_id":"VEND-OAK","memo":"Oak Paper Co. rush reprint"}' \
  --ttl 600
python3 -m exeat check \
  --policy examples/finance-assistant/agent-policy.yaml \
  --manifest examples/finance-assistant/tool-manifest.yaml \
  --approvals /tmp/exeat-replay/approvals.jsonl \
  --audit /tmp/exeat-replay/audit.jsonl \
  --agent finance-assistant \
  --tool ledger.transfer \
  --args '{"amount_usd":900,"vendor_id":"VEND-OAK","memo":"a different memo"}'
```

Expiry, same files, after the grant has expired (`--ttl 1`, then wait, then `check` with the original arguments):

```bash
python3 -m exeat approve \
  --policy examples/finance-assistant/agent-policy.yaml \
  --manifest examples/finance-assistant/tool-manifest.yaml \
  --approvals /tmp/exeat-replay/expired.jsonl \
  --audit /tmp/exeat-replay/audit.jsonl \
  --agent finance-assistant \
  --tool ledger.transfer \
  --args '{"amount_usd":900,"vendor_id":"VEND-OAK","memo":"Oak Paper Co. rush reprint"}' \
  --ttl 1
sleep 2
python3 -m exeat check \
  --policy examples/finance-assistant/agent-policy.yaml \
  --manifest examples/finance-assistant/tool-manifest.yaml \
  --approvals /tmp/exeat-replay/expired.jsonl \
  --audit /tmp/exeat-replay/audit.jsonl \
  --agent finance-assistant \
  --tool ledger.transfer \
  --args '{"amount_usd":900,"vendor_id":"VEND-OAK","memo":"Oak Paper Co. rush reprint"}'
```

## Results
