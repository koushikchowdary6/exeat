# SPDX-License-Identifier: MIT
"""Forward allowed MCP calls to an upstream HTTP server.

The Authorization header is copied as it arrived. This module does not parse
the token, store it, or include it in errors.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol

from exeat.brand import VERSION
from exeat.errors import ExeatError, ToolError

_PROTOCOL = "2024-11-05"


class RpcTransport(Protocol):
    def exchange(self, url: str, payload: bytes, headers: Mapping[str, str]) -> tuple[int, str, bytes]:
        """POST one JSON-RPC body. Return status, content type, and body bytes."""


class UrllibTransport:
    def __init__(self, timeout: float = 30) -> None:
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect())

    def exchange(self, url: str, payload: bytes, headers: Mapping[str, str]) -> tuple[int, str, bytes]:
        request = urllib.request.Request(url, data=payload, method="POST")
        for key, value in headers.items():
            request.add_header(key, value)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                return int(response.status), content_type, response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            content_type = exc.headers.get("Content-Type", "") if exc.headers is not None else ""
            return int(exc.code), content_type, body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


class HttpUpstream:
    """MCP client for one upstream URL. ``authorization`` is the raw header value."""

    def __init__(self, url: str, *, transport: RpcTransport | None = None) -> None:
        if not url.startswith(("https://", "http://")):
            raise ExeatError("upstream MCP URL must start with http:// or https://")
        self.url = url
        self.authorization: str | None = None
        self._transport = transport or UrllibTransport()
        self._lock = threading.Lock()
        self._next_id = 0
        self._tools: set[str] | None = None
        self._ready_authorization: str | None = None
        self._ready = False

    def set_authorization(self, value: str | None) -> None:
        """Replace the header that will be forwarded. The value is not logged."""

        with self._lock:
            if value != self.authorization:
                self.authorization = value
                self._ready = False
                self._tools = None

    def __repr__(self) -> str:
        return f"HttpUpstream(url={self.url!r}, authorization={'set' if self.authorization else 'absent'})"

    def supports(self, name: str) -> bool:
        with self._lock:
            self._ensure_locked()
            tools = self._tools or set()
            return name in tools

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        with self._lock:
            self._ensure_locked()
            response = self._rpc_locked("tools/call", {"name": name, "arguments": _jsonable(arguments)})
        if "error" in response:
            error = response["error"]
            message = "upstream error"
            if isinstance(error, dict):
                message = str(error.get("message", message))
            raise ExeatError(message)
        raw_result = response.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        text = _content_text(result)
        if result.get("isError"):
            raise ToolError(text or "upstream tool error")
        return text

    def _ensure_locked(self) -> None:
        if self._ready and self._ready_authorization == self.authorization and self._tools is not None:
            return
        initialized = self._rpc_locked(
            "initialize",
            {
                "protocolVersion": _PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "exeat", "version": VERSION},
            },
        )
        if "error" in initialized:
            raise ExeatError("upstream initialize failed")
        self._rpc_notify_locked("notifications/initialized", {})
        listed = self._rpc_locked("tools/list", {})
        raw_result = listed.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        raw_tools = result.get("tools")
        tools = raw_tools if isinstance(raw_tools, list) else []
        names: set[str] = set()
        for tool in tools:
            if isinstance(tool, dict) and tool.get("name"):
                names.add(str(tool["name"]))
        self._tools = names
        self._ready = True
        self._ready_authorization = self.authorization

    def _rpc_locked(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        message = self._post_locked({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        if message.get("id") != request_id and "error" not in message:
            raise ExeatError("upstream response id did not match the request")
        return message

    def _rpc_notify_locked(self, method: str, params: dict[str, Any]) -> None:
        self._post_locked({"jsonrpc": "2.0", "method": method, "params": params}, expect_body=False)

    def _post_locked(self, payload: dict[str, Any], *, expect_body: bool = True) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.authorization is not None:
            headers["Authorization"] = self.authorization
        try:
            status, content_type, raw = self._transport.exchange(self.url, body, headers)
        except OSError as exc:
            raise ExeatError("upstream MCP request failed") from exc
        if not expect_body and status < 400:
            return {}
        if status >= 400:
            raise ExeatError(f"upstream returned HTTP {status}")
        return _decode_message(raw, content_type)


def _decode_message(raw: bytes, content_type: str) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type:
        text = _sse_data(text)
    try:
        message = json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise ExeatError("upstream sent a non-JSON body") from exc
    if not isinstance(message, dict):
        raise ExeatError("upstream sent a non-object message")
    return message


def _sse_data(body: str) -> str:
    chunks: list[str] = []
    for line in body.splitlines():
        if line.startswith("data:"):
            chunks.append(line[5:].lstrip())
    if not chunks:
        raise ExeatError("upstream event stream contained no data")
    return "\n".join(chunks)


def _content_text(result: dict[str, Any]) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return json.dumps(result)
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
