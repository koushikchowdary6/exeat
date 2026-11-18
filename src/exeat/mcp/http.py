# SPDX-License-Identifier: MIT
"""MCP over HTTP with an SSE response stream.

The client opens ``GET /sse``. The first event names the POST endpoint.
Client JSON-RPC is ``POST /messages?session=<id>``. Responses are SSE events
on the open stream. No extra service is required.
"""

from __future__ import annotations

import json
import queue
import secrets
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from exeat.brand import PRODUCT_SLUG
from exeat.mcp.server import Session, handle_message


class HttpGateway:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory
        self.sessions: dict[str, tuple[Session, queue.Queue[dict[str, Any] | None]]] = {}
        self._lock = threading.Lock()

    def open_session(self, authorization: str | None = None) -> tuple[str, queue.Queue[dict[str, Any] | None]]:
        session_id = secrets.token_hex(8)
        box: queue.Queue[dict[str, Any] | None] = queue.Queue()
        session = self.session_factory()
        apply_authorization(session, authorization)
        with self._lock:
            self.sessions[session_id] = (session, box)
        return session_id, box

    def close_session(self, session_id: str) -> None:
        with self._lock:
            self.sessions.pop(session_id, None)

    def post(self, session_id: str, message: dict[str, Any], authorization: str | None = None) -> None:
        with self._lock:
            found = self.sessions.get(session_id)
        if found is None:
            raise KeyError(session_id)
        session, box = found
        if authorization is not None:
            apply_authorization(session, authorization)
        response = handle_message(message, session)
        if response is not None:
            box.put(response)


def serve_http(gateway: HttpGateway, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _handler(gateway))
    server.serve_forever()
    return server


def make_http_server(gateway: HttpGateway, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _handler(gateway))


def _handler(gateway: HttpGateway) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path != "/sse":
                self.send_error(404)
                return
            session_id, box = gateway.open_session(_authorization(self))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            endpoint = f"/messages?session={session_id}"
            self.wfile.write(f"event: endpoint\ndata: {endpoint}\n\n".encode())
            self.wfile.flush()
            try:
                while True:
                    item = box.get()
                    if item is None:
                        break
                    payload = json.dumps(item)
                    self.wfile.write(f"event: message\ndata: {payload}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            finally:
                gateway.close_session(session_id)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/messages":
                self.send_error(404)
                return
            query = parse_qs(parsed.query)
            session_ids = query.get("session") or []
            if not session_ids:
                self.send_error(400, "session is required")
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                message = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(400, "invalid JSON")
                return
            if not isinstance(message, dict):
                self.send_error(400, "JSON object required")
                return
            try:
                gateway.post(session_ids[0], message, _authorization(self))
            except KeyError:
                self.send_error(404, "unknown session")
                return
            body = b'{"ok":true}'
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    return Handler


def apply_authorization(session: Session, authorization: str | None) -> None:
    """Give the backend the exact Authorization header value. Never log it."""

    backend = getattr(session, "backend", None)
    setter = getattr(backend, "set_authorization", None)
    if authorization is not None and callable(setter):
        setter(authorization)


def _authorization(handler: BaseHTTPRequestHandler) -> str | None:
    value = handler.headers.get("Authorization")
    if value is None or not value.strip():
        return None
    return value


def server_banner(host: str, port: int) -> str:
    return f"{PRODUCT_SLUG} mcp http  http://{host}:{port}/sse"
