# SPDX-License-Identifier: MIT
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml

from exeat.adapters.crewai_tools import as_crew_tool
from exeat.adapters.langchain_tools import as_tool
from exeat.adapters.openai_agents import function_tool
from exeat.cli import main
from exeat.decision import ApprovalRequired, Check, Decision, Denied, Effect
from exeat.demo.backend import FinanceBackend
from exeat.errors import ExeatError
from exeat.explain import render_explanation
from exeat.ledger import Ledger
from exeat.locate import finance_example_dir
from exeat.mcp.http import HttpGateway, make_http_server
from exeat.mcp.server import Session
from exeat.policy.cases import render_case_report, run_cases
from exeat.runtime.leash import Leash
from exeat.signing import sign_file, verify_file
from exeat.webhook import notify_hold

ROOT = finance_example_dir()
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _leash(tmp_path: Path) -> Leash:
    return Leash.open(
        policy_path=ROOT / "agent-policy.yaml",
        manifest_path=ROOT / "tool-manifest.yaml",
        agent="finance-assistant",
        audit_path=tmp_path / "audit.jsonl",
        approval_path=tmp_path / "approvals.jsonl",
        ledger_path=tmp_path / "ledger.sqlite",
    )


def test_case_file_errors_and_mismatches(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(ExeatError, match="could not read"):
        run_cases(missing)
    broken = tmp_path / "broken.yaml"
    broken.write_text(":\n - [", encoding="utf-8")
    with pytest.raises(ExeatError, match="parse"):
        run_cases(broken)
    not_map = tmp_path / "list.yaml"
    not_map.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(ExeatError, match="mapping"):
        run_cases(not_map)
    (tmp_path / "agent-policy.yaml").write_text(
        (ROOT / "agent-policy.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "tool-manifest.yaml").write_text(
        (ROOT / "tool-manifest.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    empty = tmp_path / "empty-cases.yaml"
    empty.write_text("cases: []\n", encoding="utf-8")
    with pytest.raises(ExeatError, match="non-empty"):
        run_cases(empty)
    bad_policy = tmp_path / "bad.yaml"
    bad_policy.write_text("policy: nope.yaml\ncases:\n  - name: x\n", encoding="utf-8")
    with pytest.raises(ExeatError):
        run_cases(bad_policy)
    cases = tmp_path / "cases.yaml"
    cases.write_text(
        """
policy: agent-policy.yaml
manifest: tool-manifest.yaml
cases:
  - not-a-map
  - name: missing-fields
    agent: finance-assistant
  - name: bad-args
    agent: finance-assistant
    tool: files.read
    arguments: []
    expect: deny
  - name: wrong-effect
    agent: finance-assistant
    tool: files.read
    arguments:
      path: secrets/api-keys.txt
    expect: allow
  - name: wrong-rule
    agent: finance-assistant
    tool: files.read
    arguments:
      path: data/finance/invoices/INV-1042.json
    expect: allow
    rule: not-the-rule
""",
        encoding="utf-8",
    )
    count, failures = run_cases(cases, now=NOW)
    assert count == 5
    assert len(failures) == 5
    report = render_case_report(cases, count, failures)
    assert "FAIL" in report
    assert "passed" in render_case_report(cases, 1, [])


def test_signing_rejects_bad_inputs(tmp_path: Path) -> None:
    policy = tmp_path / "agent-policy.yaml"
    policy.write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(ExeatError, match="no signature"):
        verify_file(policy, tmp_path / "missing.pub")
    (policy.name + ".sig" and policy.with_name(policy.name + ".sig")).write_text("not base64!\n", encoding="utf-8")
    (tmp_path / "policy.pub").write_text("not a key\n", encoding="utf-8")
    with pytest.raises(ExeatError, match="base64"):
        verify_file(policy, tmp_path / "policy.pub")
    policy.with_name(policy.name + ".sig").write_text("YQ==\n", encoding="utf-8")
    with pytest.raises(ExeatError, match="public key"):
        verify_file(policy, tmp_path / "policy.pub")
    (tmp_path / "policy.key").write_text("nope\n", encoding="utf-8")
    with pytest.raises(ExeatError, match="private key"):
        sign_file(policy, tmp_path / "policy.key")
    with pytest.raises(ExeatError, match="could not read"):
        sign_file(tmp_path / "absent.yaml", tmp_path / "policy.key")


def test_ledger_edges(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    opened = Ledger.open(f"sqlite:///{path}")
    denied = opened.try_reserve(
        agent="ada",
        tool="pay",
        amount_cents=10,
        per_call=None,
        per_day=10,
        total=10,
        now=NOW,
    )
    assert denied.rule == "spend.no_ceiling"
    per_call = opened.try_reserve(
        agent="ada",
        tool="pay",
        amount_cents=500,
        per_call=100,
        per_day=1000,
        total=1000,
        now=NOW,
    )
    assert per_call.rule == "spend.per_call"
    ok = opened.try_reserve(
        agent="ada",
        tool="pay",
        amount_cents=80,
        per_call=100,
        per_day=100,
        total=100,
        now=NOW,
    )
    assert ok.ok and ok.reservation_id
    over_total = opened.try_reserve(
        agent="ada",
        tool="pay",
        amount_cents=30,
        per_call=100,
        per_day=1000,
        total=100,
        now=NOW,
    )
    assert over_total.rule == "spend.total"
    opened.release(ok.reservation_id)
    assert opened.totals("ada", NOW) == (0, 0)
    opened.close()
    with pytest.raises(ExeatError, match="sqlite: or postgres"):
        Ledger.open("memory://nope")
    with pytest.raises(ExeatError, match="psycopg"):
        Ledger.postgres("postgresql://localhost/none")


def test_http_error_paths(tmp_path: Path) -> None:
    leash = _leash(tmp_path)
    server = make_http_server(HttpGateway(lambda: Session(leash, FinanceBackend(ROOT), leash.manifest)))
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        with pytest.raises(HTTPError):
            urlopen(f"http://{host}:{port}/nope")
        request = Request(f"http://{host}:{port}/messages", data=b"{}", method="POST")
        with pytest.raises(HTTPError):
            urlopen(request)
        bad = Request(f"http://{host}:{port}/messages?session=missing", data=b"not-json", method="POST")
        with pytest.raises(HTTPError):
            urlopen(bad)
    finally:
        server.shutdown()
        server.server_close()


def test_adapter_fallbacks_and_hold(tmp_path: Path) -> None:
    leash = _leash(tmp_path)

    def pay(amount_usd: int, vendor_id: str, memo: str = "") -> dict[str, int]:
        return {"amount_usd": amount_usd}

    guarded = function_tool(leash, "ledger.transfer")(pay)
    with pytest.raises(ApprovalRequired):
        guarded(amount_usd=900, vendor_id="VEND-OAK")
    plain = as_tool(leash, "files.read", lambda path: path)
    assert plain(path="data/finance/invoices/INV-1042.json").endswith("INV-1042.json")
    crew = as_crew_tool(leash, "files.read", lambda path: path, description="read")
    with pytest.raises(Denied):
        crew(path="notes/todo.txt")
    assert notify_hold("http://127.0.0.1:1/nope", _decision(leash)) is False


def _decision(leash: Leash) -> Decision:
    return leash.decide("files.read", {"path": "secrets/api-keys.txt"})


def test_explain_without_trace() -> None:
    decision = Decision(
        effect=Effect.DENY,
        rule="tool.undeclared",
        reason="Denied.",
        agent="ada",
        role="clerk",
        tool="shell.exec",
        fingerprint="abc",
        policy_hash="deadbeef",
        checks=(),
    )
    text = render_explanation(decision)
    assert "no trace" in text
    assert isinstance(Check("pass", "ok", "fine"), Check)


def test_pre_commit_hook(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    policy = repo / "agent-policy.yaml"
    manifest = repo / "tool-manifest.yaml"
    policy.write_text((ROOT / "agent-policy.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    manifest.write_text((ROOT / "tool-manifest.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, env=env)
    assert main(["hook", "--repo", str(repo)]) == 0
    widened = (ROOT / "fixtures" / "escalated-policy.yaml").read_text(encoding="utf-8")
    policy.write_text(widened, encoding="utf-8")
    (ROOT / "fixtures" / "escalated-manifest.yaml").read_text(encoding="utf-8")
    manifest.write_text(
        (ROOT / "fixtures" / "escalated-manifest.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert main(["hook", "--repo", str(repo)]) == 1
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "agent-policy.yaml").write_text(policy.read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "init"], cwd=fresh, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=fresh, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "only"], cwd=fresh, check=True, capture_output=True, env=env)
    # The file matches HEAD, so the hook has nothing to diff.
    assert main(["hook", "--repo", str(fresh)]) == 0


def test_dashboard_deny_endpoint(tmp_path: Path) -> None:
    import json
    import threading
    from urllib.request import Request as UrlRequest

    from exeat.dashboard.server import Board, make_server

    board = Board(
        policy_path=ROOT / "agent-policy.yaml",
        manifest_path=ROOT / "tool-manifest.yaml",
        audit_path=tmp_path / "audit.jsonl",
        approval_path=tmp_path / "approvals.jsonl",
        sandbox=ROOT,
    )
    server = make_server(board)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        payload = json.dumps(
            {
                "agent": "finance-assistant",
                "tool": "ledger.transfer",
                "arguments": {"amount_usd": 180, "vendor_id": "VEND-OAK"},
                "by": "ada",
            }
        ).encode()
        request = UrlRequest(f"http://{host}:{port}/api/deny", data=payload, method="POST")
        with urlopen(request) as response:
            body = json.loads(response.read().decode())
        assert str(body["id"]).startswith("den_")
    finally:
        server.shutdown()
        server.server_close()


def test_yaml_roundtrip_unused() -> None:
    assert yaml.safe_load("a: 1") == {"a": 1}
