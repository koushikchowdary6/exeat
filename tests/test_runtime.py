# SPDX-License-Identifier: MIT
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentleash.decision import ApprovalRequired, Denied, Effect
from agentleash.locate import finance_example_dir
from agentleash.runtime.clock import FrozenClock
from agentleash.runtime.leash import Leash

ROOT = finance_example_dir()
DAY = datetime(2026, 4, 2, 15, 0, tzinfo=UTC)


def open_leash(tmp_path: Path, agent: str = "finance-assistant", when: datetime = DAY) -> Leash:
    clock = FrozenClock(when)
    return Leash.open(
        policy_path=ROOT / "agent-policy.yaml",
        manifest_path=ROOT / "tool-manifest.yaml",
        agent=agent,
        audit_path=tmp_path / "audit.jsonl",
        approval_path=tmp_path / "approvals.jsonl",
        clock=clock,
    )


def test_daily_ceiling_and_release(tmp_path: Path) -> None:
    leash = open_leash(tmp_path)

    def pay(amount: int, memo: str = "x") -> tuple[object, object]:
        return leash.run(
            "ledger.transfer",
            {"amount_usd": amount, "vendor_id": "VEND-OAK", "memo": memo},
            lambda: {"ok": True},
        )

    held, _ = pay(1000, "first")
    assert held.effect is Effect.REQUIRE_APPROVAL  # type: ignore[attr-defined]
    leash.approvals.grant(
        agent="finance-assistant",
        tool="ledger.transfer",
        arguments={"amount_usd": 1000, "vendor_id": "VEND-OAK", "memo": "first"},
        by="ada",
    )
    approved, _ = pay(1000, "first")
    assert approved.rule == "approval.matched"  # type: ignore[attr-defined]
    leash.approvals.grant(
        agent="finance-assistant",
        tool="ledger.transfer",
        arguments={"amount_usd": 1000, "vendor_id": "VEND-OAK", "memo": "second"},
        by="ada",
    )
    approved_again, _ = pay(1000, "second")
    assert approved_again.effect is Effect.ALLOW  # type: ignore[attr-defined]
    over_day, _ = pay(600, "too much today")
    assert over_day.rule == "spend.per_day"  # type: ignore[attr-defined]

    def boom() -> dict[str, bool]:
        raise RuntimeError("ledger down")

    with pytest.raises(RuntimeError, match="ledger down"):
        leash.run(
            "ledger.transfer",
            {"amount_usd": 100, "vendor_id": "VEND-OAK", "memo": "retry"},
            boom,
        )
    events = leash.audit.read()
    assert any(event.get("effect") == "release" for event in events)
    recovered, _ = pay(100, "recovered")
    assert recovered.effect is Effect.ALLOW  # type: ignore[attr-defined]


def test_next_utc_day_resets_the_daily_ceiling_only(tmp_path: Path) -> None:
    leash = open_leash(tmp_path)
    leash.run("ledger.transfer", {"amount_usd": 600, "vendor_id": "VEND-OAK"}, lambda: {"ok": True})
    later = Leash.open(
        policy_path=ROOT / "agent-policy.yaml",
        manifest_path=ROOT / "tool-manifest.yaml",
        agent="finance-assistant",
        audit_path=leash.audit.path,
        approval_path=leash.approvals.path,
        clock=FrozenClock(DAY + timedelta(days=1)),
    )
    decision, _ = later.run(
        "ledger.transfer",
        {"amount_usd": 600, "vendor_id": "VEND-OAK"},
        lambda: {"ok": True},
    )
    assert decision.effect is Effect.ALLOW
    assert "Today $600.00" in decision.reason
    assert "Total $1,200.00" in decision.reason


def test_decorator_raises_and_releases(tmp_path: Path) -> None:
    leash = open_leash(tmp_path)
    calls = {"n": 0}

    @leash.guard("ledger.transfer")
    def transfer(amount_usd: int, vendor_id: str, memo: str = "") -> dict[str, int]:
        calls["n"] += 1
        if amount_usd == 50:
            raise RuntimeError("nope")
        return {"amount_usd": amount_usd}

    assert transfer(amount_usd=20, vendor_id="VEND-OAK")["amount_usd"] == 20
    try:
        transfer(amount_usd=900, vendor_id="VEND-OAK")
    except ApprovalRequired as exc:
        assert exc.decision.effect is Effect.REQUIRE_APPROVAL
    else:
        raise AssertionError("expected a hold")
    try:
        transfer(amount_usd=5000, vendor_id="VEND-OAK")
    except Denied as exc:
        assert exc.decision.rule == "spend.per_call"
    else:
        raise AssertionError("expected a deny")
    assert calls["n"] == 1
    with pytest.raises(RuntimeError, match="nope"):
        transfer(amount_usd=50, vendor_id="VEND-OAK")
    decision, _ = leash.run(
        "ledger.transfer",
        {"amount_usd": 50, "vendor_id": "VEND-OAK"},
        lambda: {"ok": True},
    )
    assert decision.effect is Effect.ALLOW


def test_approval_is_bound_to_the_arguments(tmp_path: Path) -> None:
    leash = open_leash(tmp_path)
    leash.approvals.grant(
        agent="finance-assistant",
        tool="ledger.transfer",
        arguments={"amount_usd": 900, "vendor_id": "VEND-OAK", "memo": "one"},
        by="ada",
    )
    other, _ = leash.run(
        "ledger.transfer",
        {"amount_usd": 900, "vendor_id": "VEND-OAK", "memo": "two"},
        lambda: {"ok": True},
    )
    assert other.effect is Effect.REQUIRE_APPROVAL
    expired = Leash.open(
        policy_path=ROOT / "agent-policy.yaml",
        manifest_path=ROOT / "tool-manifest.yaml",
        agent="finance-assistant",
        audit_path=tmp_path / "audit-b.jsonl",
        approval_path=tmp_path / "approvals.jsonl",
        clock=FrozenClock(DAY + timedelta(hours=2)),
    )
    stale, _ = expired.run(
        "ledger.transfer",
        {"amount_usd": 900, "vendor_id": "VEND-OAK", "memo": "one"},
        lambda: {"ok": True},
    )
    assert stale.effect is Effect.REQUIRE_APPROVAL


def test_audit_log_is_append_only(tmp_path: Path) -> None:
    leash = open_leash(tmp_path)
    leash.run("files.read", {"path": "data/finance/invoices/INV-1042.json"}, lambda: {"ok": True})
    first = leash.audit.path.read_text(encoding="utf-8")
    leash.run("files.read", {"path": "notes/todo.txt"}, lambda: {"ok": True})
    second = leash.audit.path.read_text(encoding="utf-8")
    assert second.startswith(first)
    assert first.count("\n") == 1
    assert '"effect":"deny"' in second
