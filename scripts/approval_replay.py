#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run four approval-replay cases and print the gate's response.

This script does not score the responses. Read them, then fill in
docs/experiments/approval-replay.md.
"""

from __future__ import annotations

import argparse
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from exeat.decision import Decision
from exeat.jsonl import read_jsonl
from exeat.locate import finance_example_dir
from exeat.runtime.leash import Leash

CALL = {
    "amount_usd": 900,
    "vendor_id": "VEND-OAK",
    "memo": "Oak Paper Co. rush reprint",
}
OTHER_CALL = {
    "amount_usd": 900,
    "vendor_id": "VEND-OAK",
    "memo": "a different memo",
}
CASES = ("different-arguments", "expired", "concurrent", "different-agent")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print observed approval-replay outcomes. Does not judge them.")
    parser.add_argument("--case", choices=CASES, action="append", dest="cases")
    args = parser.parse_args(argv)
    selected = args.cases or list(CASES)
    for name in selected:
        print(f"== {name} ==")
        for line in _run(name):
            print(line)
        print()
    return 0


def _run(name: str) -> list[str]:
    with tempfile.TemporaryDirectory(prefix=f"exeat-replay-{name}-") as raw:
        directory = Path(raw)
        if name == "different-arguments":
            return _different_arguments(directory)
        if name == "expired":
            return _expired(directory)
        if name == "concurrent":
            return _concurrent(directory)
        if name == "different-agent":
            return _different_agent(directory)
    raise SystemExit(f"unknown case {name}")


def _different_arguments(directory: Path) -> list[str]:
    leash = _leash(directory, "finance-assistant")
    granted = leash.approvals.grant(
        agent="finance-assistant",
        tool="ledger.transfer",
        arguments=dict(CALL),
        by="operator",
        ttl_seconds=600,
    )
    decision, _ = leash.run("ledger.transfer", dict(OTHER_CALL), lambda: {"ok": True})
    return [
        _grant_line(granted),
        "replay_arguments: memo changed",
        _decision_line(decision),
        _consumed_line(directory, granted["id"]),
    ]


def _expired(directory: Path) -> list[str]:
    leash = _leash(directory, "finance-assistant")
    granted = leash.approvals.grant(
        agent="finance-assistant",
        tool="ledger.transfer",
        arguments=dict(CALL),
        by="operator",
        ttl_seconds=1,
    )
    time.sleep(2)
    decision, _ = leash.run("ledger.transfer", dict(CALL), lambda: {"ok": True})
    return [
        _grant_line(granted),
        "waited_seconds: 2",
        _decision_line(decision),
        _consumed_line(directory, granted["id"]),
    ]


def _concurrent(directory: Path) -> list[str]:
    first = _leash(directory, "finance-assistant")
    second = _leash(directory, "finance-assistant")
    granted = first.approvals.grant(
        agent="finance-assistant",
        tool="ledger.transfer",
        arguments=dict(CALL),
        by="operator",
        ttl_seconds=600,
    )
    barrier = threading.Barrier(2)
    found: list[Decision] = []
    errors: list[BaseException] = []

    def attempt() -> None:
        try:
            barrier.wait(timeout=5)
            decision, _ = _leash(directory, "finance-assistant").run(
                "ledger.transfer",
                dict(CALL),
                lambda: {"ok": True},
            )
            found.append(decision)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=attempt, name=f"replay-{index}") for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    lines = [_grant_line(granted), "callers: 2 overlapping finance-assistant calls, same arguments"]
    if errors:
        lines.append(f"errors: {errors!r}")
    for index, decision in enumerate(found, start=1):
        lines.append(f"caller_{index}: {_decision_line(decision)}")
    lines.append(_consumed_line(directory, granted["id"]))
    if len(found) != 2:
        lines.append(f"finished_callers: {len(found)}")
    # Keep the opened ledgers referenced until the calls finish.
    del first, second
    return lines


def _different_agent(directory: Path) -> list[str]:
    assistant = _leash(directory, "finance-assistant")
    intern = _leash(directory, "finance-intern")
    granted = assistant.approvals.grant(
        agent="finance-assistant",
        tool="ledger.transfer",
        arguments=dict(CALL),
        by="operator",
        ttl_seconds=600,
    )
    decision, _ = intern.run("ledger.transfer", dict(CALL), lambda: {"ok": True})
    return [
        _grant_line(granted),
        "replay_agent: finance-intern",
        _decision_line(decision),
        _consumed_line(directory, granted["id"]),
    ]


def _leash(directory: Path, agent: str) -> Leash:
    root = finance_example_dir()
    return Leash.open(
        policy_path=root / "agent-policy.yaml",
        manifest_path=root / "tool-manifest.yaml",
        agent=agent,
        audit_path=directory / "audit.jsonl",
        approval_path=directory / "approvals.jsonl",
    )


def _grant_line(record: dict[str, Any]) -> str:
    return (
        f"approval: id={record['id']} agent={record['agent']} "
        f"fingerprint={record['fingerprint']} expires={record['expires']}"
    )


def _decision_line(decision: Decision) -> str:
    return f"effect={decision.effect.value} rule={decision.rule} reason={decision.reason}"


def _consumed_line(directory: Path, approval_id: str) -> str:
    rows = read_jsonl(directory / "approvals.jsonl")
    count = sum(1 for row in rows if row.get("type") == "consumed" and row.get("approval_id") == approval_id)
    return f"consumed_records_for_approval: {count}"


if __name__ == "__main__":
    raise SystemExit(main())
