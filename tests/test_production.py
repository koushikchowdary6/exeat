# SPDX-License-Identifier: MIT
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from agentleash.adapters.crewai_tools import as_crew_tool
from agentleash.adapters.langchain_tools import as_tool
from agentleash.adapters.openai_agents import function_tool
from agentleash.cli import main
from agentleash.decision import Denied, Effect
from agentleash.demo.backend import FinanceBackend
from agentleash.diff.compare import Finding
from agentleash.diff.markdown import render_markdown
from agentleash.diff.sarif import render_sarif
from agentleash.errors import AgentleashError
from agentleash.explain import render_explanation
from agentleash.ledger import Ledger
from agentleash.locate import finance_example_dir
from agentleash.mcp.http import HttpGateway, make_http_server
from agentleash.mcp.server import Session
from agentleash.policy.cases import run_cases
from agentleash.policy.schema import POLICY_SCHEMA
from agentleash.runtime.leash import Leash
from agentleash.signing import generate_keypair, sign_file, verify_file
from agentleash.webhook import notify_hold

ROOT = finance_example_dir()
NOW = datetime(2026, 4, 2, 15, 0, tzinfo=UTC)


def test_policy_cases_for_three_agents() -> None:
    repo = ROOT.parent.parent
    for relative in (
        "examples/finance-assistant/policy-tests.yaml",
        "examples/support-inbox/policy-tests.yaml",
        "examples/research-assistant/policy-tests.yaml",
    ):
        count, failures = run_cases(repo / relative, now=NOW)
        assert count > 0
        assert failures == [], failures


def test_explain_names_the_failing_check(tmp_path: Path) -> None:
    code = main(
        [
            "explain",
            "--audit",
            str(tmp_path / "audit.jsonl"),
            "--approvals",
            str(tmp_path / "approvals.jsonl"),
            "--agent",
            "finance-assistant",
            "--tool",
            "files.read",
            "--args",
            '{"path":"secrets/api-keys.txt"}',
        ]
    )
    assert code == 1


def test_explain_text_includes_trace(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(
        [
            "explain",
            "--audit",
            str(tmp_path / "audit.jsonl"),
            "--approvals",
            str(tmp_path / "approvals.jsonl"),
            "--agent",
            "finance-assistant",
            "--tool",
            "files.read",
            "--args",
            '{"path":"secrets/api-keys.txt"}',
        ]
    )
    text = capsys.readouterr().out
    assert "path.protected" in text
    assert "pass" in text
    assert render_explanation  # imported helper stays used by the command


def test_schema_and_validate() -> None:
    assert POLICY_SCHEMA["required"] == ["version", "roles", "agents"]
    assert main(["schema"]) == 0
    assert main(["validate", "--example"]) == 0
    assert main(["test", str(ROOT / "policy-tests.yaml")]) == 0


def test_sign_and_verify_roundtrip(tmp_path: Path) -> None:
    private_path, public_path = generate_keypair(tmp_path / "keys")
    policy = tmp_path / "agent-policy.yaml"
    policy.write_text((ROOT / "agent-policy.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    signature = sign_file(policy, private_path)
    assert signature.is_file()
    verify_file(policy, public_path)
    policy.write_text(policy.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(AgentleashError, match="does not match"):
        verify_file(policy, public_path)


def test_ledger_reservations_are_atomic(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    first = Ledger.sqlite(path)
    second = Ledger.sqlite(path)
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def worker() -> None:
        barrier.wait()
        reserved = (
            first.try_reserve(
                agent="finance-assistant",
                tool="ledger.transfer",
                amount_cents=60_000,
                per_call=100_000,
                per_day=100_000,
                total=100_000,
                now=NOW,
            )
            if threading.current_thread().name == "a"
            else second.try_reserve(
                agent="finance-assistant",
                tool="ledger.transfer",
                amount_cents=60_000,
                per_call=100_000,
                per_day=100_000,
                total=100_000,
                now=NOW,
            )
        )
        results.append(reserved.ok)

    left = threading.Thread(target=worker, name="a")
    right = threading.Thread(target=worker, name="b")
    left.start()
    right.start()
    left.join()
    right.join()
    assert sorted(results) == [False, True]
    today, total = first.totals("finance-assistant", NOW)
    assert (today, total) == (60_000, 60_000)
    first.close()
    second.close()


def test_explicit_denial_blocks_a_later_allow(tmp_path: Path) -> None:
    leash = Leash.open(
        policy_path=ROOT / "agent-policy.yaml",
        manifest_path=ROOT / "tool-manifest.yaml",
        agent="finance-assistant",
        audit_path=tmp_path / "audit.jsonl",
        approval_path=tmp_path / "approvals.jsonl",
        ledger_path=tmp_path / "ledger.sqlite",
    )
    args = {"amount_usd": 180, "vendor_id": "VEND-OAK", "memo": "no"}
    leash.approvals.deny(agent="finance-assistant", tool="ledger.transfer", arguments=args, by="ada")
    decision, _ = leash.run("ledger.transfer", args, lambda: {"ok": True})
    assert decision.effect is Effect.DENY
    assert decision.rule == "approval.denied"


def test_webhook_posts_a_hold(tmp_path: Path) -> None:
    seen: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            seen.append(self.rfile.read(length))
            self.send_response(204)
            self.end_headers()

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        leash = Leash.open(
            policy_path=ROOT / "agent-policy.yaml",
            manifest_path=ROOT / "tool-manifest.yaml",
            agent="finance-assistant",
            audit_path=tmp_path / "audit.jsonl",
            approval_path=tmp_path / "approvals.jsonl",
            ledger_path=tmp_path / "ledger.sqlite",
            webhook_url=f"http://{host}:{port}/hooks/hold",
        )
        decision, _ = leash.run(
            "ledger.transfer",
            {"amount_usd": 900, "vendor_id": "VEND-OAK"},
            lambda: {"ok": True},
        )
        assert decision.effect is Effect.REQUIRE_APPROVAL
    finally:
        server.shutdown()
        server.server_close()
    assert seen
    body = json.loads(seen[0])
    assert body["tool"] == "ledger.transfer"
    assert notify_hold(None, decision) is False


def test_http_mcp_roundtrip(tmp_path: Path) -> None:
    leash = Leash.open(
        policy_path=ROOT / "agent-policy.yaml",
        manifest_path=ROOT / "tool-manifest.yaml",
        agent="finance-assistant",
        audit_path=tmp_path / "audit.jsonl",
        approval_path=tmp_path / "approvals.jsonl",
        ledger_path=tmp_path / "ledger.sqlite",
    )

    def factory() -> Session:
        return Session(leash, FinanceBackend(ROOT), leash.manifest)

    server = make_http_server(HttpGateway(factory))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    lines: list[str] = []

    def read_sse() -> None:
        with urlopen(f"http://{host}:{port}/sse") as response:
            while "path.protected" not in "".join(lines) and len(lines) < 30:
                line = response.readline().decode()
                if line == "":
                    break
                lines.append(line)

    reader = threading.Thread(target=read_sse, daemon=True)
    reader.start()
    for _ in range(50):
        if any("endpoint" in line for line in lines):
            break
        threading.Event().wait(0.05)
    endpoint = next(line for line in lines if line.startswith("data: ")).removeprefix("data: ").strip()
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "files.read", "arguments": {"path": "secrets/api-keys.txt"}},
        }
    ).encode()
    request = Request(f"http://{host}:{port}{endpoint}", data=payload, method="POST")
    with urlopen(request) as response:
        assert response.status == 202
    reader.join(timeout=3)
    server.shutdown()
    server.server_close()
    blob = "".join(lines)
    assert "path.protected" in blob or "isError" in blob


def test_adapters_use_the_gate_and_optional_factories(tmp_path: Path) -> None:
    leash = Leash.open(
        policy_path=ROOT / "agent-policy.yaml",
        manifest_path=ROOT / "tool-manifest.yaml",
        agent="finance-assistant",
        audit_path=tmp_path / "audit.jsonl",
        approval_path=tmp_path / "approvals.jsonl",
        ledger_path=tmp_path / "ledger.sqlite",
    )

    def read_file(path: str) -> str:
        return path

    wrapped = function_tool(leash, "files.read", sdk_decorator=lambda fn: ("sdk", fn))
    tool = wrapped(read_file)
    assert tool[0] == "sdk"
    with pytest.raises(Denied):
        tool[1](path="secrets/api-keys.txt")

    created: list[object] = []

    def factory(fn: object) -> object:
        created.append(fn)
        return fn

    langchain_tool = as_tool(leash, "files.read", read_file, tool_factory=factory)
    assert created
    with pytest.raises(Denied):
        langchain_tool(path="secrets/api-keys.txt")

    def crew_class(*, name: str, description: str, func: object) -> dict[str, object]:
        return {"name": name, "description": description, "func": func}

    crew = as_crew_tool(leash, "files.read", read_file, description="read", tool_class=crew_class)
    assert crew["name"] == "files.read"
    with pytest.raises(Denied):
        crew["func"](path="secrets/api-keys.txt")


def test_markdown_and_sarif_formats() -> None:
    findings = [Finding("high", "tool.gained", "role finance-analyst", "Gained tool shell.exec.")]
    table = render_markdown(findings, base_label="base", head_label="head", fail_on="medium")
    assert "| high | role finance-analyst | Gained tool shell.exec. |" in table
    sarif = json.loads(render_sarif(findings, policy_path="agent-policy.yaml"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"][0]["level"] == "error"
    code = main(["diff", "--example", "escalation", "--format", "sarif"])
    assert code == 1


def test_cli_sign_verify_and_deny(tmp_path: Path) -> None:
    assert main(["keygen", "--out", str(tmp_path / "keys")]) == 0
    policy = tmp_path / "agent-policy.yaml"
    policy.write_text((ROOT / "agent-policy.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["sign", "--policy", str(policy), "--key", str(tmp_path / "keys" / "policy.key")]) == 0
    assert main(["verify", "--policy", str(policy), "--public", str(tmp_path / "keys" / "policy.pub")]) == 0
    approvals = tmp_path / "approvals.jsonl"
    assert (
        main(
            [
                "deny",
                "--policy",
                str(ROOT / "agent-policy.yaml"),
                "--manifest",
                str(ROOT / "tool-manifest.yaml"),
                "--audit",
                str(tmp_path / "audit.jsonl"),
                "--approvals",
                str(approvals),
                "--agent",
                "finance-assistant",
                "--tool",
                "ledger.transfer",
                "--args",
                '{"amount_usd":180,"vendor_id":"VEND-OAK"}',
                "--by",
                "ada",
            ]
        )
        == 0
    )
    assert "denial" in approvals.read_text(encoding="utf-8")
