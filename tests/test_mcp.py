# SPDX-License-Identifier: MIT
import json
import os
import select
import subprocess
import sys
from pathlib import Path

from exeat.demo.backend import FinanceBackend
from exeat.locate import finance_example_dir
from exeat.mcp.server import Session, handle_message
from exeat.runtime.leash import Leash


def test_handle_message_allow_and_deny(tmp_path: Path) -> None:
    root = finance_example_dir()
    leash = Leash.open(
        policy_path=root / "agent-policy.yaml",
        manifest_path=root / "tool-manifest.yaml",
        agent="finance-assistant",
        audit_path=tmp_path / "audit.jsonl",
        approval_path=tmp_path / "approvals.jsonl",
    )
    session = Session(leash, FinanceBackend(root), leash.manifest)
    init = handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        session,
    )
    assert init is not None
    assert init["result"]["serverInfo"]["name"] == "exeat"
    assert handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, session) is None
    listed = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session)
    assert listed is not None
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert "ledger.transfer" in names
    assert "shell.exec" not in names
    allowed = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "files.read",
                "arguments": {"path": "data/finance/invoices/INV-1042.json"},
            },
        },
        session,
    )
    assert allowed is not None
    assert allowed["result"]["isError"] is False
    denied = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "shell.exec", "arguments": {"command": "id"}},
        },
        session,
    )
    assert denied is not None
    assert denied["result"]["isError"] is True
    assert "not declared" in denied["result"]["content"][0]["text"]


def test_serve_writes_parse_errors_and_unknown_methods(tmp_path: Path) -> None:
    from io import StringIO

    from exeat.mcp.server import serve

    root = finance_example_dir()
    leash = Leash.open(
        policy_path=root / "agent-policy.yaml",
        manifest_path=root / "tool-manifest.yaml",
        agent="finance-assistant",
        audit_path=tmp_path / "audit.jsonl",
        approval_path=tmp_path / "approvals.jsonl",
    )
    session = Session(leash, FinanceBackend(root), leash.manifest)
    stdin = StringIO(
        "\n"
        "not-json\n"
        + json.dumps(["nope"])
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": 1}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 3, "method": "unknown"})
        + "\n"
    )
    stdout = StringIO()
    serve(session, stdin, stdout)
    lines = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1]["result"] == {}
    assert lines[2]["error"]["code"] == -32602
    assert lines[3]["error"]["code"] == -32601


def test_stdio_roundtrip() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "exeat", "mcp", "--example", "--agent", "finance-assistant"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin and proc.stdout
    try:
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        init = _recv(proc)
        assert init["result"]["capabilities"]["tools"]["listChanged"] is False
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "http.get", "arguments": {"url": "https://evil.test/collect"}},
            },
        )
        denied = _recv(proc)
        assert denied["result"]["isError"] is True
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_proxy_does_not_forward_a_denied_call(tmp_path: Path) -> None:
    policy = tmp_path / "agent-policy.yaml"
    manifest = tmp_path / "tool-manifest.yaml"
    policy.write_text(
        """
version: 1
roles:
  - name: reader
    tools: [ledger.read]
    scopes:
      files:
        allow: ["data/finance/**"]
agents:
  - name: clerk
    role: reader
""",
        encoding="utf-8",
    )
    manifest.write_text(
        """
version: 1
tools:
  - name: ledger.read
    arguments:
      - {name: invoice_id, type: string, required: true}
      - {name: path, type: path, required: true}
  - name: shell.exec
    arguments:
      - {name: command, type: string, required: true}
""",
        encoding="utf-8",
    )
    log = tmp_path / "upstream.log"
    env = {**os.environ, "UPSTREAM_LOG": str(log)}
    upstream = Path(__file__).parent / "support" / "upstream_server.py"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "exeat",
            "mcp",
            "--policy",
            str(policy),
            "--manifest",
            str(manifest),
            "--agent",
            "clerk",
            "--audit",
            str(tmp_path / "audit.jsonl"),
            "--approvals",
            str(tmp_path / "approvals.jsonl"),
            "--",
            sys.executable,
            str(upstream),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdin and proc.stdout
    try:
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        _recv(proc)
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "shell.exec", "arguments": {"command": "id"}},
            },
        )
        denied = _recv(proc)
        assert denied["result"]["isError"] is True
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "ledger.read",
                    "arguments": {"invoice_id": "INV-1", "path": "data/finance/invoices/INV-1.json"},
                },
            },
        )
        allowed = _recv(proc)
        assert allowed["result"]["isError"] is False
        assert "ok:ledger.read" in allowed["result"]["content"][0]["text"]
    finally:
        proc.kill()
        proc.wait(timeout=5)
    forwarded = log.read_text(encoding="utf-8") if log.exists() else ""
    assert "ledger.read" in forwarded
    assert "shell.exec" not in forwarded


def _send(proc: subprocess.Popen[str], message: dict[str, object]) -> None:
    assert proc.stdin
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


def _recv(proc: subprocess.Popen[str], timeout: float = 5) -> dict[str, object]:
    assert proc.stdout
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    if not ready:
        err = ""
        if proc.stderr:
            ready_err, _, _ = select.select([proc.stderr], [], [], 0.1)
            if ready_err:
                err = proc.stderr.read()
        raise AssertionError(f"timed out waiting for MCP response\n{err}")
    line = proc.stdout.readline()
    assert line, "empty MCP response"
    loaded = json.loads(line)
    assert isinstance(loaded, dict)
    return loaded
