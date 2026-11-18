# SPDX-License-Identifier: MIT
"""Forward allowed calls to an upstream MCP server on stdio.

The upstream is a subprocess. Its stdout is the protocol stream and is never
inherited by the outer server, so the two JSON-RPC conversations stay apart.
"""

from __future__ import annotations

import json
import subprocess
import threading
from typing import Any

from exeat.errors import ExeatError, ToolError


class ProxyBackend:
    def __init__(self, command: list[str]) -> None:
        if not command:
            raise ExeatError("upstream command is empty")
        self.command = command
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._drain_stderr, name="exeat-upstream-stderr", daemon=True).start()
        self._lock = threading.Lock()
        self._next_id = 0
        self._tools: set[str] = set()
        initialized = self.rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "exeat", "version": "0.1.0"},
            },
        )
        if "error" in initialized:
            raise ExeatError(f"upstream initialize failed: {initialized['error']}")
        self.notify("notifications/initialized", {})
        listed = self.rpc("tools/list", {})
        raw_result = listed.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        raw_tools = result.get("tools")
        tools = raw_tools if isinstance(raw_tools, list) else []
        names: set[str] = set()
        for tool in tools:
            if isinstance(tool, dict) and tool.get("name"):
                names.add(str(tool["name"]))
        self._tools = names

    def supports(self, name: str) -> bool:
        return name in self._tools

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        response = self.rpc("tools/call", {"name": name, "arguments": _jsonable(arguments)})
        if "error" in response:
            message = response["error"].get("message", "upstream error")
            raise ExeatError(str(message))
        raw_result = response.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        text = _content_text(result)
        if result.get("isError"):
            raise ToolError(text or "upstream tool error")
        return text

    def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            while True:
                message = self._read()
                if message.get("id") == request_id:
                    return message

    def notify(self, method: str, params: dict[str, Any]) -> None:
        with self._lock:
            self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _drain_stderr(self) -> None:
        if self._proc.stderr is None:
            return
        for _line in self._proc.stderr:
            continue

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _write(self, payload: dict[str, Any]) -> None:
        if self._proc.stdin is None:
            raise ExeatError("upstream stdin is closed")
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def _read(self) -> dict[str, Any]:
        if self._proc.stdout is None:
            raise ExeatError("upstream stdout is closed")
        line = self._proc.stdout.readline()
        if line == "":
            raise ExeatError("upstream closed the protocol stream")
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ExeatError("upstream sent a non-JSON line") from exc
        if not isinstance(message, dict):
            raise ExeatError("upstream sent a non-object message")
        return message


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
