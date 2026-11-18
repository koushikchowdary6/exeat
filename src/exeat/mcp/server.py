# SPDX-License-Identifier: MIT
"""A small MCP server. Every tools/call is decided by the leash first.

Stdio messages are newline-delimited JSON-RPC. Logs go to stderr so the
protocol stream stays clean. This is the middleware shape: the process in
front of the tools, not a scanner that hopes the model behaves.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any, Protocol, TextIO

from exeat.brand import PRODUCT_SLUG, VERSION
from exeat.decision import Effect
from exeat.errors import ExeatError, ToolError
from exeat.policy.model import Manifest, ToolSpec
from exeat.runtime.leash import Leash

_PROTOCOL = "2024-11-05"


class ToolBackend(Protocol):
    def supports(self, name: str) -> bool:
        """Whether this backend can execute ``name``."""

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """Run a tool that policy has already allowed."""


class Session:
    def __init__(self, leash: Leash, backend: ToolBackend, manifest: Manifest) -> None:
        self.leash = leash
        self.backend = backend
        self.manifest = manifest


def serve(session: Session, stdin: TextIO, stdout: TextIO) -> None:
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write(stdout, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if not isinstance(message, dict):
            continue
        response = handle_message(message, session)
        if response is not None:
            _write(stdout, response)


def handle_message(message: Mapping[str, Any], session: Session) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if not isinstance(method, str):
        return _error(request_id, -32600, "invalid request")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        raw_params = message.get("params")
        params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        version = params.get("protocolVersion")
        protocol = version if isinstance(version, str) else _PROTOCOL
        return _result(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": PRODUCT_SLUG, "version": VERSION},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": _advertised(session)})
    if method == "tools/call":
        raw_call = message.get("params")
        call_params: dict[str, Any] = raw_call if isinstance(raw_call, dict) else {}
        name = call_params.get("name")
        arguments = call_params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error(request_id, -32602, "tools/call needs a name and an arguments object")
        return _result(request_id, _call(session, name, arguments))
    if request_id is None:
        return None
    return _error(request_id, -32601, f"method not found: {method}")


def _call(session: Session, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not session.backend.supports(name):
        decision = session.leash.decide(name, arguments)
        session.leash.audit.record(decision, commit=False)
        if decision.effect is not Effect.ALLOW:
            return _tool_error(decision.reason)
        return _tool_error(f"{name} is allowed by policy but this backend does not provide it.")
    try:
        decision, result = session.leash.run(
            name,
            arguments,
            lambda: session.backend.call(name, arguments),
        )
    except (ToolError, ExeatError) as exc:
        return _tool_error(str(exc))
    if decision.effect is not Effect.ALLOW:
        return _tool_error(decision.reason)
    return _tool_ok(result)


def _advertised(session: Session) -> list[dict[str, Any]]:
    granted = session.leash.effective.tools
    tools: list[dict[str, Any]] = []
    for spec in session.manifest.tools:
        if spec.name in granted and session.backend.supports(spec.name):
            tools.append(_schema(spec))
    return tools


def _schema(spec: ToolSpec) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for arg in spec.arguments:
        properties[arg.name] = {
            "type": _json_type(arg.type),
            "description": arg.description or arg.type,
        }
        if arg.required:
            required.append(arg.name)
    return {
        "name": spec.name,
        "description": spec.description,
        "inputSchema": {"type": "object", "properties": properties, "required": required},
    }


def _json_type(kind: str) -> str:
    if kind in {"spend", "number"}:
        return "number"
    if kind == "boolean":
        return "boolean"
    return "string"


def _tool_ok(result: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": _as_text(result)}], "isError": False}


def _tool_error(reason: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": reason}], "isError": True}


def _as_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, indent=2, default=str)


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _write(stdout: TextIO, payload: dict[str, Any]) -> None:
    stdout.write(json.dumps(payload, default=str) + "\n")
    stdout.flush()


def serve_stdio(session: Session) -> None:
    print(f"{PRODUCT_SLUG} mcp gate for {session.leash.agent}", file=sys.stderr, flush=True)
    serve(session, sys.stdin, sys.stdout)
