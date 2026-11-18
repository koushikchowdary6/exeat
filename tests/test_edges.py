# SPDX-License-Identifier: MIT
import json
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml

from agentleash.canon import canon, fingerprint, truncate
from agentleash.cli import main
from agentleash.dashboard.server import Board, make_server
from agentleash.errors import AgentleashError, PolicyError
from agentleash.jsonl import read_jsonl
from agentleash.locate import finance_example_dir
from agentleash.mcp.proxy import ProxyBackend
from agentleash.money import format_usd, parse_cents
from agentleash.policy.evaluate import evaluate
from agentleash.policy.load import load_policy, parse_manifest, parse_policy
from agentleash.runtime.clock import FrozenClock

NOW = datetime(2026, 4, 2, 15, 0, tzinfo=UTC)


def test_money_and_canon() -> None:
    assert parse_cents(12, field="amount") == 1200
    assert parse_cents("12.50", field="amount") == 1250
    assert parse_cents(Decimal("1.2"), field="amount") == 120
    assert format_usd(None) == "unset"
    assert format_usd(-150) == "-$1.50"
    for bad in (True, None, -1, "nope", [], float("nan"), 1.234):
        with pytest.raises(PolicyError):
            parse_cents(bad, field="amount")
    assert canon({"b": 1, "a": True, "nested": [Decimal("2")]}) == {"a": True, "b": "1.00", "nested": ["2.00"]}
    assert canon(float("nan")) == "nan"
    assert "truncated" in truncate("x" * 600)
    assert len(fingerprint("ada", "files.read", {"path": "a"})) == 20


def test_proxy_backend_lists_and_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "upstream.log"
    monkeypatch.setenv("UPSTREAM_LOG", str(log))
    upstream = Path(__file__).parent / "support" / "upstream_server.py"
    backend = ProxyBackend([sys.executable, str(upstream)])
    try:
        assert backend.supports("ledger.read")
        assert backend.supports("shell.exec")
        assert "ok:ledger.read" in str(backend.call("ledger.read", {"invoice_id": "INV-1", "path": "data/finance/a"}))
        refused = backend.rpc("nope", {})
        assert "error" in refused
    finally:
        backend.close()
    assert "ledger.read" in log.read_text(encoding="utf-8")


def test_cli_approve_audit_and_bad_diff(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = finance_example_dir()
    audit = tmp_path / "audit.jsonl"
    approvals = tmp_path / "approvals.jsonl"
    assert (
        main(
            [
                "check",
                "--policy",
                str(root / "agent-policy.yaml"),
                "--manifest",
                str(root / "tool-manifest.yaml"),
                "--agent",
                "finance-assistant",
                "--tool",
                "files.read",
                "--args",
                '{"path":"data/finance/invoices/INV-1042.json"}',
            ]
        )
        == 0
    )
    assert main(["diff"]) == 2
    assert main(["check", "--agent", "finance-assistant", "--tool", "files.read", "--args", "not-json"]) == 2
    code = main(
        [
            "approve",
            "--policy",
            str(root / "agent-policy.yaml"),
            "--manifest",
            str(root / "tool-manifest.yaml"),
            "--approvals",
            str(approvals),
            "--agent",
            "finance-assistant",
            "--tool",
            "ledger.transfer",
            "--args",
            '{"amount_usd":900,"vendor_id":"VEND-OAK"}',
            "--by",
            "ada",
        ]
    )
    assert code == 0
    assert "apr_" in capsys.readouterr().out
    main(
        [
            "demo",
            "--audit",
            str(audit),
            "--approvals",
            str(tmp_path / "demo-approvals.jsonl"),
            "--keep",
        ]
    )
    assert main(["audit", "--audit", str(audit)]) == 0
    tape = capsys.readouterr().out
    assert "allow" in tape
    assert main(["audit", "--audit", str(tmp_path / "missing.jsonl")]) == 0


def test_load_errors(tmp_path: Path) -> None:
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(PolicyError, match="empty"):
        load_policy(empty)
    broken = tmp_path / "broken.yaml"
    broken.write_text(":\n  - [", encoding="utf-8")
    with pytest.raises(PolicyError, match="parse"):
        load_policy(broken)
    missing = tmp_path / "nope.yaml"
    with pytest.raises(PolicyError, match="could not read"):
        load_policy(missing)
    with pytest.raises(PolicyError, match="version"):
        parse_policy({"version": 2, "roles": [], "agents": []})
    with pytest.raises(PolicyError, match="duplicate"):
        parse_policy(
            yaml.safe_load(
                """
version: 1
roles:
  - name: clerk
    tools: [files.read]
  - name: clerk
    tools: [files.read]
agents:
  - name: ada
    role: clerk
"""
            )
        )


def test_more_decisions() -> None:
    policy = parse_policy(
        yaml.safe_load(
            """
version: 1
roles:
  - name: clerk
    tools: [files.read, ledger.transfer, http.get, ledger.query]
    spend:
      per_call: 10
      per_day: 10
      total: 10
    scopes:
      files:
        allow: ["data/**"]
      urls:
        allow: ["https://ledger.internal.example/**"]
      tables:
        allow: ["finance.invoices"]
    approvals:
      - name: always-read
        tools: [files.read]
        when:
          always: true
        reason: Reads wait.
agents:
  - name: ada
    role: clerk
"""
        )
    )
    manifest = parse_manifest(
        yaml.safe_load(
            """
version: 1
tools:
  - name: files.read
    arguments:
      - {name: path, type: path, required: true}
  - name: ledger.transfer
    arguments:
      - {name: amount_usd, type: spend, required: true}
      - {name: vendor_id, type: string, required: true}
  - name: http.get
    arguments:
      - {name: url, type: url, required: true}
  - name: ledger.query
    arguments:
      - {name: table, type: table, required: true}
"""
        )
    )
    missing = evaluate(policy, manifest, "ada", "files.read", {}, now=NOW)
    assert missing.rule == "args.missing"
    absolute = evaluate(policy, manifest, "ada", "files.read", {"path": "/etc/passwd"}, now=NOW)
    assert absolute.rule == "path.invalid"
    held = evaluate(policy, manifest, "ada", "files.read", {"path": "data/a"}, now=NOW)
    assert held.rule == "approval.required"
    total = evaluate(
        policy,
        manifest,
        "ada",
        "ledger.transfer",
        {"amount_usd": 6, "vendor_id": "V"},
        history=[
            {
                "agent": "ada",
                "effect": "allow",
                "committed": True,
                "id": "dec_old",
                "spend_cents": 600,
                "ts": "2026-04-01T01:00:00Z",
            }
        ],
        now=NOW,
    )
    assert total.rule == "spend.total"
    weird = evaluate(policy, manifest, "ada", "http.get", {"url": "file:///tmp/x"}, now=NOW)
    assert weird.rule == "url.invalid"
    slash = evaluate(policy, manifest, "ada", "ledger.query", {"table": "finance/invoices"}, now=NOW)
    assert slash.rule == "table.invalid"
    bad_args = evaluate(policy, manifest, "ada", "files.read", ["nope"], now=NOW)  # type: ignore[arg-type]
    assert bad_args.rule == "args.invalid"


def test_corrupt_audit_line(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text("{}\nnot-json\n", encoding="utf-8")
    with pytest.raises(AgentleashError, match="not valid JSON"):
        read_jsonl(path)


def test_dashboard_errors_and_approval(tmp_path: Path) -> None:
    import threading

    root = finance_example_dir()
    board = Board(
        policy_path=root / "agent-policy.yaml",
        manifest_path=root / "tool-manifest.yaml",
        audit_path=tmp_path / "audit.jsonl",
        approval_path=tmp_path / "approvals.jsonl",
        sandbox=root,
    )
    server = make_server(board)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        with urlopen(f"http://{host}:{port}/api/health") as response:
            assert json.loads(response.read())["ok"] is True
        with pytest.raises(HTTPError):
            urlopen(f"http://{host}:{port}/missing")
        held = _post(
            host,
            port,
            "/api/check",
            {
                "agent": "finance-assistant",
                "tool": "ledger.transfer",
                "arguments": {"amount_usd": 900, "vendor_id": "VEND-OAK"},
            },
        )
        assert held["effect"] == "require_approval"
        approved = _post(
            host,
            port,
            "/api/approve",
            {
                "agent": "finance-assistant",
                "tool": "ledger.transfer",
                "arguments": {"amount_usd": 900, "vendor_id": "VEND-OAK"},
                "by": "ada",
            },
        )
        assert str(approved["id"]).startswith("apr_")
        allowed = _post(
            host,
            port,
            "/api/check",
            {
                "agent": "finance-assistant",
                "tool": "ledger.transfer",
                "arguments": {"amount_usd": 900, "vendor_id": "VEND-OAK"},
            },
        )
        assert allowed["effect"] == "allow"
        bad = _raw(host, port, "/api/check", b"not-json")
        assert bad["error"]
    finally:
        server.shutdown()
        server.server_close()


def _post(host: str, port: int, path: str, payload: dict[str, object]) -> dict[str, object]:
    return _raw(host, port, path, json.dumps(payload).encode())


def _raw(host: str, port: int, path: str, body: bytes) -> dict[str, object]:
    request = Request(
        f"http://{host}:{port}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request) as response:
            loaded = json.loads(response.read().decode())
    except Exception as exc:
        if hasattr(exc, "read"):
            loaded = json.loads(exc.read().decode())  # type: ignore[attr-defined]
        else:
            raise
    assert isinstance(loaded, dict)
    return loaded


def test_spend_ignores_garbage_history() -> None:
    from agentleash.spend import committed_spend

    today, total = committed_spend(
        [
            {"agent": "ada", "effect": "allow", "committed": True, "id": "a", "spend_cents": True, "ts": "nope"},
            {"agent": "ada", "effect": "allow", "committed": True, "id": "b", "spend_cents": 100, "ts": "2026-04-02"},
            {
                "agent": "other",
                "effect": "allow",
                "committed": True,
                "id": "c",
                "spend_cents": 500,
                "ts": "2026-04-02T00:00:00Z",
            },
            {"agent": "ada", "effect": "release", "reverses": "missing"},
        ],
        agent="ada",
        now=NOW,
    )
    assert (today, total) == (100, 100)


def test_frozen_clock_rejects_naive_time() -> None:
    with pytest.raises(ValueError):
        FrozenClock(datetime(2026, 1, 1))


def test_diff_file_mode(capsys: pytest.CaptureFixture[str]) -> None:
    root = finance_example_dir()
    code = main(
        [
            "diff",
            "--base-policy",
            str(root / "agent-policy.yaml"),
            "--head-policy",
            str(root / "fixtures" / "narrowed-policy.yaml"),
            "--base-manifest",
            str(root / "tool-manifest.yaml"),
            "--head-manifest",
            str(root / "tool-manifest.yaml"),
        ]
    )
    assert code == 0
    assert "No risky escalation" in capsys.readouterr().out
    _ = os.environ.get("NO_COLOR")
