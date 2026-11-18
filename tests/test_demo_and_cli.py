# SPDX-License-Identifier: MIT
import json
from pathlib import Path

from exeat.cli import main
from exeat.decision import Effect
from exeat.demo.run import run_demo
from exeat.demo.world import INVOICE


def test_demo_script_matches_every_expected_decision(tmp_path: Path) -> None:
    results = run_demo(audit_path=tmp_path / "audit.jsonl", approval_path=tmp_path / "approvals.jsonl")
    assert results
    assert all(result.ok for result in results), [
        (result.step.id, result.decision.effect, result.decision.rule, result.decision.reason)
        for result in results
        if not result.ok
    ]
    read = next(result for result in results if result.step.id == "read-invoice")
    assert read.output is not None
    assert INVOICE["memo"] in str(read.output)
    shell = next(result for result in results if result.step.id == "injected-shell")
    assert shell.decision.effect is Effect.DENY
    assert shell.output is None


def test_cli_demo_diff_check_and_chart(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    approvals = tmp_path / "approvals.jsonl"
    assert main(["demo", "--audit", str(audit), "--approvals", str(approvals)]) == 0
    assert '"effect":"allow"' in audit.read_text(encoding="utf-8")
    assert main(["diff", "--example", "escalation"]) == 1
    assert main(["diff", "--example", "narrowing"]) == 0
    text = _capture(["diff", "--example", "escalation", "--format", "json"])
    payload = json.loads(text)
    assert payload["fail"] is True
    assert payload["counts"]["high"] >= 1
    assert main(["chart"]) == 0
    chart = _capture(["chart"])
    assert "finance-assistant" in chart
    assert "finance-intern" in chart
    assert (
        main(
            [
                "check",
                "--agent",
                "finance-assistant",
                "--tool",
                "files.read",
                "--args",
                '{"path":"secrets/api-keys.txt"}',
            ]
        )
        == 1
    )
    assert (
        main(
            [
                "check",
                "--audit",
                str(tmp_path / "clean-audit.jsonl"),
                "--approvals",
                str(tmp_path / "clean-approvals.jsonl"),
                "--agent",
                "finance-assistant",
                "--tool",
                "ledger.transfer",
                "--args",
                '{"amount_usd":900,"vendor_id":"VEND-OAK"}',
            ]
        )
        == 3
    )


def _capture(argv: list[str]) -> str:
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        main(argv)
    return buffer.getvalue()
