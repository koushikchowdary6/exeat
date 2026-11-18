# SPDX-License-Identifier: MIT
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from exeat.cli import main
from exeat.errors import ExeatError, PolicyError
from exeat.integrations.encord.demo import run_encord_demo
from exeat.integrations.encord.endpoints import MERLIN_MCP_URLS, merlin_mcp_url
from exeat.integrations.encord.mock import MOCK_NOTICE, TOOL_NAMES, MockMerlin, make_mock_server
from exeat.locate import encord_example_dir
from exeat.mcp.upstream_http import HttpUpstream
from exeat.policy.cases import run_cases
from exeat.policy.evaluate import evaluate
from exeat.policy.load import load_manifest, load_policy, parse_policy
from exeat.runtime.audit import AuditLog, export_audit, verify_chain
from exeat.runtime.clock import FrozenClock
from exeat.runtime.leash import Leash
from exeat.signing import verify_file

ROOT = encord_example_dir()
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def test_merlin_policy_is_signed_and_cases_pass() -> None:
    verify_file(ROOT / "agent-policy.yaml", ROOT / "policy.pub")
    count, failures = run_cases(ROOT / "policy-tests.yaml", now=NOW)
    assert count == 7
    assert failures == []
    policy = load_policy(ROOT / "agent-policy.yaml")
    manifest = load_manifest(ROOT / "tool-manifest.yaml")
    assert [tool.name for tool in manifest.tools] == list(TOOL_NAMES)
    assert "unofficial" in policy.agent("merlin-assistant").description.lower()


def test_rate_limit_counts_holds_and_ignores_bad_timestamps() -> None:
    policy = load_policy(ROOT / "agent-policy.yaml")
    manifest = load_manifest(ROOT / "tool-manifest.yaml")
    arguments = {"project_id": "proj_atlas", "user_email": "agent@example.test", "role": "admin"}
    history = [
        {"agent": "merlin-assistant", "tool": "delete_project", "effect": "require_approval", "ts": "not-a-time"},
        {
            "agent": "merlin-assistant",
            "tool": "add_project_collaborator",
            "effect": "require_approval",
            "ts": (NOW - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        {
            "agent": "merlin-assistant",
            "tool": "set_project_collaborator_role",
            "effect": "require_approval",
            "ts": (NOW - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        {
            "agent": "other",
            "tool": "delete_project",
            "effect": "allow",
            "ts": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    ]
    decision = evaluate(
        policy,
        manifest,
        "merlin-assistant",
        "set_project_collaborator_role",
        arguments,
        history=history,
        now=NOW,
    )
    assert decision.effect.value == "deny"
    assert decision.rule == "rate.per_minute"


def test_agent_cannot_declare_rates_and_per_minute_must_be_positive() -> None:
    with pytest.raises(PolicyError, match="rates belongs on a role"):
        parse_policy(
            {
                "version": 1,
                "roles": [{"name": "reader", "tools": ["list_projects"]}],
                "agents": [{"name": "bot", "role": "reader", "rates": []}],
            }
        )
    with pytest.raises(PolicyError, match="per_minute"):
        parse_policy(
            {
                "version": 1,
                "roles": [
                    {
                        "name": "reader",
                        "tools": ["list_projects"],
                        "rates": [{"name": "reads", "tools": ["list_projects"], "per_minute": True}],
                    }
                ],
                "agents": [{"name": "bot", "role": "reader"}],
            }
        )


def test_audit_chain_detects_tampering_and_exports(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    leash = Leash.open(
        policy_path=ROOT / "agent-policy.yaml",
        manifest_path=ROOT / "tool-manifest.yaml",
        agent="merlin-assistant",
        audit_path=path,
        approval_path=tmp_path / "approvals.jsonl",
        clock=FrozenClock(NOW),
    )
    decision, _ = leash.run("list_projects", {}, lambda: {"ok": True})
    assert decision.effect.value == "allow"
    assert verify_chain(path) == []
    original = path.read_text(encoding="utf-8")
    assert "merlin-demo-token" not in original
    lines = original.splitlines()
    record = json.loads(lines[0])
    record["reason"] = "tampered"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    failures = verify_chain(path)
    assert failures and "hash" in failures[0].detail
    path.write_text("", encoding="utf-8")
    assert verify_chain(path) == []
    exported = export_audit(path)
    assert exported["verified"] is True
    assert exported["records"] == []
    path.write_text(original + "\n{broken\n", encoding="utf-8")
    broken = verify_chain(path)
    assert any(item.detail == "not valid JSON" for item in broken)
    path.write_text(json.dumps({"effect": "allow"}) + "\n", encoding="utf-8")
    assert verify_chain(path)[0].detail.startswith("missing prev")
    with pytest.raises(ExeatError, match="unchained"):
        AuditLog(path).record(decision, commit=False)
    path.write_text("{broken\n", encoding="utf-8")
    with pytest.raises(ExeatError, match="not valid JSON"):
        AuditLog(path).record(decision, commit=False)


def test_upstream_forwards_the_bearer_and_hides_it(tmp_path: Path) -> None:
    token = "Bearer opaque-token-value"
    seen: list[dict[str, str]] = []

    class Transport:
        def exchange(self, url: str, payload: bytes, headers: dict[str, str]) -> tuple[int, str, bytes]:
            seen.append(dict(headers))
            message = json.loads(payload)
            method = message["method"]
            if method == "initialize":
                body = {"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": "2024-11-05"}}
            elif method == "tools/list":
                body = {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"tools": [{"name": "list_projects"}, {"name": "set_project_collaborator_role"}]},
                }
            elif method == "tools/call":
                body = {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"content": [{"type": "text", "text": "mock-ok"}], "isError": False},
                }
            else:
                body = {}
            raw = json.dumps(body).encode()
            if method == "notifications/initialized":
                return 202, "application/json", b""
            return 200, "application/json", raw

    upstream = HttpUpstream("http://127.0.0.1/mcp", transport=Transport())
    upstream.set_authorization(token)
    assert upstream.supports("list_projects") is True
    assert upstream.supports("missing") is False
    assert upstream.call("list_projects", {}) == "mock-ok"
    assert seen[0]["Authorization"] == token
    assert token not in repr(upstream)
    with pytest.raises(ExeatError, match="http:// or https://"):
        HttpUpstream("ftp://example.test/mcp")


def test_upstream_reports_failures_without_the_token() -> None:
    class Boom:
        def exchange(self, url: str, payload: bytes, headers: dict[str, str]) -> tuple[int, str, bytes]:
            method = json.loads(payload)["method"]
            if method == "initialize":
                return 500, "text/plain", b"Bearer opaque-token-value exploded"
            raise AssertionError(method)

    upstream = HttpUpstream("https://mcp.encord.com/mcp", transport=Boom())
    upstream.set_authorization("Bearer opaque-token-value")
    with pytest.raises(ExeatError, match="HTTP 500") as exc:
        upstream.supports("list_projects")
    assert "opaque-token-value" not in str(exc.value)

    class Garbage:
        def __init__(self) -> None:
            self.mode = "sse-empty"

        def exchange(self, url: str, payload: bytes, headers: dict[str, str]) -> tuple[int, str, bytes]:
            method = json.loads(payload)["method"]
            if method == "notifications/initialized":
                return 204, "application/json", b""
            if self.mode == "sse-empty":
                return 200, "text/event-stream", b"event: message\n\n"
            if self.mode == "array":
                return 200, "application/json", b"[]"
            if self.mode == "bad-id":
                return 200, "application/json", b'{"jsonrpc":"2.0","id":999,"result":{}}'
            if self.mode == "tool-error":
                return (
                    200,
                    "application/json",
                    json.dumps(
                        {"jsonrpc": "2.0", "id": json.loads(payload)["id"], "result": {"isError": True, "content": []}}
                    ).encode(),
                )
            if self.mode == "rpc-error":
                return (
                    200,
                    "application/json",
                    json.dumps(
                        {"jsonrpc": "2.0", "id": json.loads(payload)["id"], "error": {"message": "nope"}}
                    ).encode(),
                )
            if self.mode == "string-error":
                return (
                    200,
                    "application/json",
                    json.dumps({"jsonrpc": "2.0", "id": json.loads(payload)["id"], "error": "nope"}).encode(),
                )
            if self.mode == "plain":
                return 200, "application/json", b"not-json"
            return (
                200,
                "text/event-stream",
                (
                    "data: "
                    + json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": json.loads(payload)["id"],
                            "result": {"protocolVersion": "2024-11-05", "tools": [{"name": "list_projects"}]},
                        }
                    )
                    + "\n\n"
                ).encode(),
            )

    transport = Garbage()
    client = HttpUpstream("http://127.0.0.1:9/mcp", transport=transport)
    with pytest.raises(ExeatError, match="no data"):
        client.supports("list_projects")
    transport.mode = "array"
    with pytest.raises(ExeatError, match="non-object"):
        client.supports("list_projects")
    transport.mode = "bad-id"
    with pytest.raises(ExeatError, match="did not match"):
        client.supports("list_projects")
    transport.mode = "sse"
    assert client.supports("list_projects") is True
    transport.mode = "tool-error"
    from exeat.errors import ToolError

    with pytest.raises(ToolError):
        client.call("list_projects", {})
    transport.mode = "rpc-error"
    with pytest.raises(ExeatError, match="nope"):
        client.call("list_projects", {})
    transport.mode = "string-error"
    with pytest.raises(ExeatError, match="upstream error"):
        client.call("list_projects", {})
    transport.mode = "plain"
    client._ready = False
    with pytest.raises(ExeatError, match="non-JSON"):
        client.supports("list_projects")

    class Down:
        def exchange(self, url: str, payload: bytes, headers: dict[str, str]) -> tuple[int, str, bytes]:
            raise OSError("down")

    offline = HttpUpstream("http://127.0.0.1:9/mcp", transport=Down())
    with pytest.raises(ExeatError, match="request failed"):
        offline.supports("list_projects")


def test_proxy_holds_admin_and_forwards_the_token(tmp_path: Path) -> None:
    code, transcript = run_encord_demo(audit_path=tmp_path / "audit.jsonl", approval_path=tmp_path / "approvals.jsonl")
    assert code == 0
    assert "HOLD" in transcript
    assert "audit verify  ok" in transcript
    assert "Bearer merlin-demo-token" not in transcript
    assert "Encord does not endorse" in transcript
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "Bearer merlin-demo-token" not in text
    assert verify_chain(tmp_path / "audit.jsonl") == []


def test_mock_rejects_bad_requests() -> None:
    import threading
    import urllib.error
    import urllib.request

    mock = MockMerlin()
    server = make_mock_server(mock)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    try:
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", data=b"{}")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/mcp",
            data=b"not-json",
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(request)
        unknown = urllib.request.Request(
            f"http://127.0.0.1:{port}/mcp",
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
            ).encode(),
            method="POST",
            headers={"Authorization": "Bearer test-token"},
        )
        with urllib.request.urlopen(unknown) as response:
            body = json.loads(response.read())
        assert body["result"]["isError"] is True
        assert "mock" in body["result"]["content"][0]["text"]
        assert mock.last_authorization == "Bearer test-token"
        assert MOCK_NOTICE in mock.tool_definitions()[0]["description"]
    finally:
        server.shutdown()


def test_cli_audit_and_region(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    audit = tmp_path / "audit.jsonl"
    code, _ = run_encord_demo(audit_path=audit, approval_path=tmp_path / "approvals.jsonl")
    assert code == 0
    assert main(["audit", "verify", "--audit", str(audit)]) == 0
    destination = tmp_path / "tape.json"
    assert main(["audit", "export", "--audit", str(audit), "--out", str(destination)]) == 0
    exported = json.loads(destination.read_text(encoding="utf-8"))
    assert exported["verified"] is True
    assert exported["algorithm"] == "sha256-chain"
    assert main(["mcp-http", "--region", "eu", "--upstream", "https://mcp.encord.com/mcp"]) == 2
    captured = capsys.readouterr()
    assert "audit chain ok" in captured.out
    assert merlin_mcp_url("us") == MERLIN_MCP_URLS["us"]
    with pytest.raises(ExeatError):
        merlin_mcp_url("ap")


def test_mock_shapes_and_redirects_are_explicit() -> None:
    from exeat.integrations.encord.mock import _dispatch
    from exeat.locate import _example_dir
    from exeat.mcp.sse_client import SseClientError, exchange
    from exeat.mcp.upstream_http import _NoRedirect

    mock = MockMerlin()
    assert _dispatch(mock, {"jsonrpc": "2.0", "method": "notifications/initialized"}, None) is None
    assert _dispatch(mock, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, None)["result"] == {}
    missing = _dispatch(mock, {"jsonrpc": "2.0", "id": 2, "method": "nope"}, None)
    assert missing["error"]["code"] == -32601
    bad_call = _dispatch(mock, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": 1}}, None)
    assert bad_call["error"]["code"] == -32602
    for name in TOOL_NAMES:
        payload = mock.call_tool(name, {"project_id": "proj_atlas", "query": "atlas", "group_id": "grp"}, "Bearer x")
        assert payload["isError"] is False
        assert "mock" in payload["content"][0]["text"]
    assert _NoRedirect().redirect_request(None, None, 302, "see other", {}, "https://evil.test") is None  # type: ignore[arg-type]
    with pytest.raises(ExeatError, match="could not find"):
        _example_dir("not-a-real-example")
    with pytest.raises(SseClientError):
        exchange(
            "http://127.0.0.1:1",
            [{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
            authorization="Bearer x",
            timeout=0.2,
        )


def test_encord_demo_command() -> None:
    assert main(["encord-demo"]) == 0


def test_mock_cli_starts(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from exeat.integrations.encord import mock as mock_module
    from exeat.integrations.encord.__main__ import main as mock_main

    monkeypatch.setattr(mock_module.ThreadingHTTPServer, "serve_forever", lambda self: None)
    assert mock_main(["--port", "0"]) == 0
    assert "exeat merlin mock" in capsys.readouterr().out


def test_child_role_can_replace_a_rate_limit() -> None:
    from exeat.policy.effective import effective_for_agent

    policy = parse_policy(
        {
            "version": 1,
            "roles": [
                {
                    "name": "reader",
                    "tools": ["list_projects"],
                    "rates": [{"name": "reads", "tools": ["list_projects"], "per_minute": 5}],
                },
                {
                    "name": "operator",
                    "inherits": "reader",
                    "tools": ["delete_project"],
                    "rates": [{"name": "reads", "tools": ["list_projects"], "per_minute": 1}],
                },
            ],
            "agents": [{"name": "bot", "role": "operator"}],
        }
    )
    limit = effective_for_agent(policy, "bot").rates[0]
    assert limit.per_minute == 1


def test_rate_change_is_an_escalation() -> None:
    from exeat.diff.compare import diff_documents

    base = parse_policy(
        {
            "version": 1,
            "roles": [
                {
                    "name": "reader",
                    "tools": ["list_projects"],
                    "rates": [{"name": "reads", "tools": ["list_projects"], "per_minute": 5}],
                }
            ],
            "agents": [{"name": "bot", "role": "reader"}],
        }
    )
    head = parse_policy(
        {
            "version": 1,
            "roles": [
                {
                    "name": "reader",
                    "tools": ["list_projects"],
                    "rates": [{"name": "reads", "tools": ["list_projects", "get_project"], "per_minute": 50}],
                }
            ],
            "agents": [{"name": "bot", "role": "reader"}],
        }
    )
    manifest = load_manifest(ROOT / "tool-manifest.yaml")
    findings = diff_documents(base, head, manifest, manifest)
    codes = {item.code for item in findings}
    assert "rate.loosened" in codes
    assert "rate.covered" in codes
    tightened = diff_documents(head, base, manifest, manifest)
    tightened_codes = {item.code for item in tightened}
    assert "rate.tightened" in tightened_codes
    assert "rate.uncovered" in tightened_codes
    removed = parse_policy(
        {
            "version": 1,
            "roles": [{"name": "reader", "tools": ["list_projects"]}],
            "agents": [{"name": "bot", "role": "reader"}],
        }
    )
    assert any(item.code == "rate.removed" for item in diff_documents(base, removed, manifest, manifest))
