# SPDX-License-Identifier: MIT
"""Local permission board. Binds to loopback and does not phone home."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from exeat.brand import PRODUCT_SLUG, PRODUCT_TITLE
from exeat.dashboard.snapshot import build_snapshot
from exeat.demo.backend import FinanceBackend
from exeat.errors import ExeatError, ToolError
from exeat.jsonl import read_jsonl
from exeat.policy.load import load_manifest, load_policy
from exeat.runtime.leash import Leash

_STATIC = Path(__file__).resolve().parent / "static"
_MAX_BODY = 64 * 1024


class Board:
    def __init__(
        self,
        *,
        policy_path: Path,
        manifest_path: Path,
        audit_path: Path,
        approval_path: Path,
        sandbox: Path,
    ) -> None:
        self.policy_path = policy_path
        self.manifest_path = manifest_path
        self.audit_path = audit_path
        self.approval_path = approval_path
        self.sandbox = sandbox

    def snapshot(self) -> dict[str, Any]:
        policy = load_policy(self.policy_path)
        manifest = load_manifest(self.manifest_path)
        events = read_jsonl(self.audit_path) if self.audit_path.exists() else []
        include = (self.sandbox / "agent-policy.yaml").is_file() and self.policy_path.resolve() == (
            self.sandbox / "agent-policy.yaml"
        ).resolve()
        return build_snapshot(policy, manifest, events, include_presets=include)

    def run_call(self, agent: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        leash = self._leash(agent)
        backend = self._backend()
        execute: Callable[[], Any] | None = None
        if backend is not None and backend.supports(tool):

            def execute(bound: FinanceBackend = backend, name: str = tool) -> Any:
                return bound.call(name, arguments)

        try:
            decision, result = leash.run(tool, arguments, execute)
        except ToolError as exc:
            return {
                "effect": "error",
                "rule": "tool.error",
                "reason": str(exc),
                "fingerprint": "",
                "spend_cents": None,
                "tool_result": None,
                "tool_error": str(exc),
            }
        return {
            "effect": decision.effect.value,
            "rule": decision.rule,
            "reason": decision.reason,
            "fingerprint": decision.fingerprint,
            "spend_cents": decision.spend_cents,
            "tool_result": result,
            "tool_error": None,
        }

    def approve(self, agent: str, tool: str, arguments: dict[str, Any], by: str) -> dict[str, Any]:
        from exeat.runtime.approvals import ApprovalLog

        record = ApprovalLog(self.approval_path).grant(
            agent=agent,
            tool=tool,
            arguments=arguments,
            by=by or "board-operator",
        )
        return {"id": record["id"], "fingerprint": record["fingerprint"], "expires": record["expires"]}

    def deny(self, agent: str, tool: str, arguments: dict[str, Any], by: str) -> dict[str, Any]:
        from exeat.runtime.approvals import ApprovalLog

        record = ApprovalLog(self.approval_path).deny(
            agent=agent,
            tool=tool,
            arguments=arguments,
            by=by or "board-operator",
        )
        return {"id": record["id"], "fingerprint": record["fingerprint"], "expires": record["expires"]}

    def _leash(self, agent: str) -> Leash:
        return Leash.open(
            policy_path=self.policy_path,
            manifest_path=self.manifest_path,
            agent=agent,
            audit_path=self.audit_path,
            approval_path=self.approval_path,
        )

    def _backend(self) -> FinanceBackend | None:
        marker = self.sandbox / "data" / "finance" / "invoices" / "INV-1042.json"
        if marker.is_file():
            return FinanceBackend(self.sandbox)
        return None


def make_server(board: Board, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _handler_factory(board))


def serve(
    *,
    host: str,
    port: int,
    policy_path: Path,
    manifest_path: Path,
    audit_path: Path,
    approval_path: Path,
    sandbox: Path,
) -> None:
    board = Board(
        policy_path=policy_path,
        manifest_path=manifest_path,
        audit_path=audit_path,
        approval_path=approval_path,
        sandbox=sandbox,
    )
    handler = _handler_factory(board)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"{PRODUCT_TITLE} board  http://{host}:{port}", flush=True)
    print("loopback only · no telemetry · Ctrl-C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("", flush=True)
    finally:
        server.server_close()


def _handler_factory(board: Board) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            try:
                if path == "/api/health":
                    self._send(200, {"ok": True, "product": PRODUCT_SLUG})
                elif path == "/api/snapshot":
                    self._send(200, board.snapshot())
                elif path in {"/", "/index.html"}:
                    self._file("index.html", "text/html; charset=utf-8", replace=True)
                elif path == "/static/styles.css":
                    self._file("styles.css", "text/css; charset=utf-8")
                elif path == "/static/app.js":
                    self._file("app.js", "text/javascript; charset=utf-8")
                else:
                    self._send(404, {"error": "not found"})
            except ExeatError as exc:
                self._send(500, {"error": str(exc)})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                payload = self._body()
                if path == "/api/check":
                    self._send(
                        200,
                        board.run_call(
                            _need(payload, "agent"),
                            _need(payload, "tool"),
                            _args(payload),
                        ),
                    )
                elif path == "/api/approve":
                    self._send(
                        200,
                        board.approve(
                            _need(payload, "agent"),
                            _need(payload, "tool"),
                            _args(payload),
                            str(payload.get("by") or "board-operator"),
                        ),
                    )
                elif path == "/api/deny":
                    self._send(
                        200,
                        board.deny(
                            _need(payload, "agent"),
                            _need(payload, "tool"),
                            _args(payload),
                            str(payload.get("by") or "board-operator"),
                        ),
                    )
                else:
                    self._send(404, {"error": "not found"})
            except ExeatError as exc:
                self._send(400, {"error": str(exc)})
            except (KeyError, TypeError, ValueError) as exc:
                self._send(400, {"error": str(exc)})

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length > _MAX_BODY:
                raise ExeatError("request body is too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                loaded = json.loads(raw.decode("utf-8"), parse_float=Decimal)
            except json.JSONDecodeError as exc:
                raise ExeatError("request body is not JSON") from exc
            if not isinstance(loaded, dict):
                raise ExeatError("request body must be an object")
            return loaded

        def _file(self, name: str, content_type: str, *, replace: bool = False) -> None:
            file_path = _STATIC / name
            if not file_path.is_file():
                self._send(500, {"error": f"missing {name}"})
                return
            body = file_path.read_bytes()
            if replace:
                text = body.decode("utf-8").replace("__PRODUCT_TITLE__", PRODUCT_TITLE)
                text = text.replace("__PRODUCT_SLUG__", PRODUCT_SLUG)
                body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _need(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExeatError(f"{key} is required")
    return value.strip()


def _args(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("arguments", {})
    if not isinstance(raw, dict):
        raise ExeatError("arguments must be an object")
    return raw
