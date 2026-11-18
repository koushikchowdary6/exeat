# SPDX-License-Identifier: MIT
"""Branches the happy-path tests do not walk: Postgres SQL, CLI servers, HTTP errors."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from exeat.cli import main
from exeat.dashboard.server import make_server
from exeat.errors import ExeatError, PolicyError
from exeat.ledger import Ledger
from exeat.locate import finance_example_dir
from exeat.mcp.http import HttpGateway, make_http_server
from exeat.policy.evaluate import evaluate
from exeat.policy.load import parse_manifest, parse_policy

NOW = datetime(2026, 6, 2, 8, 0, tzinfo=UTC)
ROOT = finance_example_dir()
ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


class _Cursor:
    def __init__(self, store: _Store, sql: str, params: tuple[object, ...]) -> None:
        self._row: object = None
        if sql.startswith("SELECT COALESCE"):
            if store.as_dict:
                self._row = {"total": store.total}
            elif store.as_none:
                self._row = None
            else:
                self._row = (store.total,)
        elif sql.startswith("SELECT agent"):
            self._row = (params[0],)
        elif sql.startswith("INSERT INTO reservations"):
            store.total += int(params[3])

    def fetchone(self) -> object:
        return self._row


class _Store:
    def __init__(self) -> None:
        self.total = 0
        self.as_dict = False
        self.as_none = False
        self.fail_insert = False
        self.statements: list[str] = []

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        self.statements.append(sql)
        if self.fail_insert and sql.startswith("INSERT INTO reservations"):
            raise RuntimeError("disk full")

    def cursor(self) -> _Cursor:
        raise AssertionError("cursor is created by the ledger")

    def commit(self) -> None:
        self.statements.append("COMMIT")

    def rollback(self) -> None:
        self.statements.append("ROLLBACK")

    def close(self) -> None:
        self.statements.append("CLOSE")


class _Connection:
    def __init__(self, store: _Store) -> None:
        self.store = store

    def execute(self, sql: str) -> None:
        self.store.execute(sql)

    def cursor(self) -> object:
        holder: dict[str, _Cursor] = {}

        class Cursor:
            def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
                self.store.execute(sql, params)  # type: ignore[attr-defined]
                holder["row"] = _Cursor(self.store, sql, params)  # type: ignore[attr-defined]

            def fetchone(self) -> object:
                return holder["row"].fetchone()

            store = self.store

        return Cursor()

    def commit(self) -> None:
        self.store.commit()

    def rollback(self) -> None:
        self.store.rollback()

    def close(self) -> None:
        self.store.close()


def test_postgres_dialect_reserves_and_rolls_back() -> None:
    store = _Store()
    ledger = Ledger(_Connection(store), "postgres")
    assert any("CREATE TABLE" in statement for statement in store.statements)
    reserved = ledger.try_reserve(
        agent="ada",
        tool="ledger.transfer",
        amount_cents=100,
        per_call=500,
        per_day=500,
        total=500,
        now=NOW,
    )
    assert reserved.ok
    assert ledger.totals("ada", NOW) == (100, 100)
    store.as_dict = True
    assert ledger.totals("ada", NOW) == (100, 100)
    store.as_dict = False
    store.as_none = True
    assert ledger.totals("ada", NOW) == (0, 0)
    store.as_none = False
    assert ledger.commit(reserved.reservation_id or "") is None
    ledger.release(reserved.reservation_id or "")
    store.fail_insert = True
    with pytest.raises(RuntimeError, match="disk full"):
        ledger.try_reserve(
            agent="ada",
            tool="ledger.transfer",
            amount_cents=100,
            per_call=500,
            per_day=500,
            total=500,
            now=NOW,
        )
    assert "ROLLBACK" in store.statements
    ledger.close()
    with pytest.raises(ExeatError, match="unknown ledger"):
        Ledger(_Connection(_Store()), "mysql")


def test_cli_servers_and_error_exits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import exeat.dashboard.server as board
    import exeat.mcp.http as http

    monkeypatch.setattr(board.ThreadingHTTPServer, "serve_forever", lambda self: None)
    monkeypatch.setattr(http.ThreadingHTTPServer, "serve_forever", lambda self: None)
    monkeypatch.setattr("exeat.cli.serve_stdio", lambda session: None)

    class DummyProxy:
        def __init__(self, command: list[str]) -> None:
            self.command = command

        def close(self) -> None:
            return None

    monkeypatch.setattr("exeat.cli.ProxyBackend", DummyProxy)
    assert main(["dashboard", "--host", "127.0.0.1", "--port", "0"]) == 0
    assert main(["mcp-http", "--host", "127.0.0.1", "--port", "0"]) == 0
    assert main(["mcp", "--", "python", "-c", "pass"]) == 0

    def _interrupt(*args: object, **kwargs: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("exeat.cli.render_chart", _interrupt)
    assert main(["chart"]) == 130
    audit = tmp_path / "audit.jsonl"
    assert (
        main(
            [
                "check",
                "--agent",
                "finance-assistant",
                "--tool",
                "files.read",
                "--args",
                "[]",
                "--audit",
                str(audit),
                "--approvals",
                str(tmp_path / "approvals.jsonl"),
            ]
        )
        == 2
    )
    sarif = tmp_path / "out.sarif"
    assert main(["diff", "--example", "narrowing", "--format", "sarif", "--sarif-output", str(sarif)]) == 0
    assert sarif.is_file()
    assert main(["diff", "--rev-base", "HEAD"]) == 2


def _request(url: str, method: str = "GET", body: bytes | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_http_and_dashboard_reject_bad_requests() -> None:
    gateway = HttpGateway(lambda: None)  # type: ignore[arg-type]
    httpd = make_http_server(gateway)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        assert _request(base + "/nope")[0] == 404
        assert _request(base + "/messages", "POST", b"{}")[0] == 400
        assert _request(base + "/messages?session=missing", "POST", b"not-json")[0] == 400
        assert _request(base + "/messages?session=missing", "POST", b"[]")[0] == 400
        assert _request(base + "/messages?session=missing", "POST", b"{}")[0] == 404
        captured: list[bytes] = []

        def read_sse() -> None:
            with urllib.request.urlopen(base + "/sse", timeout=3) as response:
                captured.append(response.read(180))

        reader = threading.Thread(target=read_sse)
        reader.start()
        for _ in range(50):
            if gateway.sessions:
                break
            threading.Event().wait(0.02)
        session_id = next(iter(gateway.sessions))
        gateway.sessions[session_id][1].put(None)
        reader.join(3)
        assert captured and b"event: endpoint" in captured[0]
    finally:
        httpd.shutdown()
        httpd.server_close()

    board = make_server(
        __import__("exeat.dashboard.server", fromlist=["Board"]).Board(
            policy_path=ROOT / "agent-policy.yaml",
            manifest_path=ROOT / "tool-manifest.yaml",
            audit_path=ROOT / ".exeat" / "audit.jsonl",
            approval_path=ROOT / ".exeat" / "approvals.jsonl",
            sandbox=ROOT,
        )
    )
    thread = threading.Thread(target=board.serve_forever, daemon=True)
    thread.start()
    port = board.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        assert _request(base + "/static/app.js")[0] == 200
        assert _request(base + "/static/styles.css")[0] == 200
        assert _request(base + "/missing")[0] == 404
        assert _request(base + "/api/check", "POST", b"not-json")[0] == 400
        assert _request(base + "/api/check", "POST", b"[]")[0] == 400
        assert _request(base + "/api/check", "POST", json.dumps({"tool": "files.read"}).encode())[0] == 400
        assert _request(base + "/api/nope", "POST", b"{}")[0] == 404
    finally:
        board.shutdown()
        board.server_close()


def test_loader_rejects_empty_and_mistyped_documents() -> None:
    with pytest.raises(PolicyError, match="at least one role"):
        parse_policy({"version": 1, "roles": [], "agents": [{"name": "ada", "role": "reader"}]})
    with pytest.raises(PolicyError, match="at least one agent"):
        parse_policy({"version": 1, "roles": [{"name": "reader", "tools": ["files.read"]}], "agents": []})
    with pytest.raises(PolicyError, match="at least one tool"):
        parse_manifest({"version": 1, "tools": []})
    with pytest.raises(PolicyError, match="risk"):
        parse_manifest({"version": 1, "tools": [{"name": "files.read", "risk": "extreme"}]})
    with pytest.raises(PolicyError, match="required"):
        parse_manifest(
            {
                "version": 1,
                "tools": [{"name": "files.read", "arguments": [{"name": "path", "type": "path", "required": "yes"}]}],
            }
        )
    with pytest.raises(PolicyError, match="always"):
        parse_policy(
            yaml.safe_load(
                """
version: 1
roles:
  - name: reader
    tools: [files.read]
    approvals:
      - name: hold
        tools: [files.read]
        reason: Wait.
        when: {always: "yes"}
agents:
  - name: ada
    role: reader
"""
            )
        )


def test_hook_skips_a_deleted_policy_and_a_new_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, **ENV}
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    policy = repo / "agent-policy.yaml"
    policy.write_text((ROOT / "agent-policy.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    manifest = (ROOT / "tool-manifest.yaml").read_text(encoding="utf-8")
    (repo / "tool-manifest.yaml").write_text(manifest, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, env=env)
    policy.unlink()
    assert main(["hook", "--repo", str(repo)]) == 0
    policy.write_text((ROOT / "agent-policy.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "tool-manifest.yaml").unlink()
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "drop manifest"], cwd=repo, check=True, capture_output=True, env=env)
    (repo / "tool-manifest.yaml").write_text(manifest, encoding="utf-8")
    text = policy.read_text(encoding="utf-8").replace("Finance assistant", "Finance assistant revised", 1)
    policy.write_text(text, encoding="utf-8")
    assert main(["hook", "--repo", str(repo)]) == 0
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert main(["hook", "--repo", str(plain)]) == 0


def test_sqlite_open_and_idle_rollback(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "ledger.sqlite"
    ledger = Ledger.open(f"sqlite:///{path}")
    ledger._rollback()
    ledger.close()
    with pytest.raises(ExeatError, match="psycopg"):
        Ledger.open("postgres://localhost/exeat")
    with pytest.raises(ExeatError, match="sqlite:"):
        Ledger.open("memory://ledger")


_NARROW = """
version: 1
roles:
  - name: reader
    tools: [files.read, http.get, ledger.query, email.send]
    scopes:
      files:
        deny: ["data/secret/**"]
      urls:
        allow: ["https://docs.example/guide", "http://docs.example:8080/guide/**"]
        deny: ["https://docs.example/guide/private"]
      tables:
        allow: ["finance.*"]
        deny: ["finance.secrets"]
    recipients:
      addresses: [ada@northwind.example]
agents:
  - name: ada
    role: reader
"""


def test_scope_edges() -> None:
    policy = parse_policy(yaml.safe_load(_NARROW))
    manifest = parse_manifest(
        yaml.safe_load(
            """
version: 1
tools:
  - name: files.read
    arguments: [{name: path, type: path, required: true}]
  - name: http.get
    arguments: [{name: url, type: url, required: true}]
  - name: ledger.query
    arguments: [{name: table, type: table, required: true}]
  - name: email.send
    arguments:
      - {name: to, type: recipient, required: true}
      - {name: subject, type: string, required: true}
      - {name: body, type: string, required: true}
"""
        )
    )

    def decide(tool: str, arguments: dict[str, object]) -> str:
        return evaluate(policy, manifest, "ada", tool, arguments, now=NOW).rule

    assert decide("files.read", {"path": "data/secret/keys.txt"}) == "path.denied"
    assert decide("files.read", {"path": "data/notes.txt"}) == "path.out_of_scope"
    assert decide("http.get", {"url": ""}) == "args.invalid"
    assert decide("http.get", {"url": "https://docs.example/guide/private"}) == "url.denied"
    assert decide("http.get", {"url": "https://other.example/guide"}) == "url.out_of_scope"
    assert decide("http.get", {"url": "https://"}) == "url.invalid"
    assert decide("http.get", {"url": "http://docs.example:8080/guide/page"}) == "allow"
    assert decide("ledger.query", {"table": ""}) == "args.invalid"
    assert decide("ledger.query", {"table": "finance.secrets"}) == "table.denied"
    assert decide("ledger.query", {"table": "other.table"}) == "table.out_of_scope"
    assert decide("email.send", {"to": "ada@northwind.example", "subject": "s", "body": "b"}) == "allow"
    assert decide("email.send", {"to": "bob@other.example", "subject": "s", "body": "b"}) == "recipient.denied"
    assert decide("email.send", {"to": ".ada@northwind.example", "subject": "s", "body": "b"}) == "recipient.invalid"
    assert decide("email.send", {"to": "ada@-bad.com", "subject": "s", "body": "b"}) == "recipient.invalid"
