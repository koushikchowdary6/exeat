# SPDX-License-Identifier: MIT
import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from agentleash.dashboard.server import Board, make_server
from agentleash.demo.run import run_demo
from agentleash.locate import finance_example_dir


def test_board_serves_the_org_chart_and_a_denied_call(tmp_path: Path) -> None:
    root = finance_example_dir()
    audit = tmp_path / "audit.jsonl"
    approvals = tmp_path / "approvals.jsonl"
    run_demo(audit_path=audit, approval_path=approvals)
    board = Board(
        policy_path=root / "agent-policy.yaml",
        manifest_path=root / "tool-manifest.yaml",
        audit_path=audit,
        approval_path=approvals,
        sandbox=root,
    )
    server = make_server(board)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        with urlopen(f"http://{host}:{port}/") as response:
            html = response.read().decode("utf-8")
        assert "Agentleash" in html
        assert "/static/app.js" in html
        with urlopen(f"http://{host}:{port}/static/styles.css") as response:
            assert "--paper" in response.read().decode("utf-8")
        with urlopen(f"http://{host}:{port}/api/snapshot") as response:
            snapshot = json.loads(response.read().decode("utf-8"))
        assert snapshot["org"]["name"] == "Northwind Books"
        assert snapshot["audit_count"] >= 1
        assert any(agent["name"] == "finance-intern" for agent in snapshot["agent_details"])
        denied = _post(
            host,
            port,
            "/api/check",
            {
                "agent": "finance-assistant",
                "tool": "files.read",
                "arguments": {"path": "secrets/api-keys.txt"},
            },
        )
        assert denied["effect"] == "deny"
        assert denied["rule"] == "path.protected"
        allowed = _post(
            host,
            port,
            "/api/check",
            {
                "agent": "finance-assistant",
                "tool": "files.read",
                "arguments": {"path": "data/finance/invoices/INV-1042.json"},
            },
        )
        assert allowed["effect"] == "allow"
        assert "Ignore previous instructions" in json.dumps(allowed["tool_result"])
    finally:
        server.shutdown()
        server.server_close()


def _post(host: str, port: int, path: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        f"http://{host}:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request) as response:
        body = json.loads(response.read().decode("utf-8"))
    assert isinstance(body, dict)
    return body
