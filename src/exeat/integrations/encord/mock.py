# SPDX-License-Identifier: MIT
"""A local stand-in for Encord Merlin's MCP server.

The tool names below are plausible shapes for listing projects and changing
collaborators, roles, and workspace groups. They were not copied from a live
``mcp.encord.com`` session. Every response says this is a mock.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from exeat.integrations.encord import UNOFFICIAL

MOCK_NAME = "exeat-merlin-mock"
MOCK_NOTICE = UNOFFICIAL + " This process is a mock. It does not call Encord."

READ_TOOLS = (
    "list_projects",
    "get_project",
    "search_projects",
    "list_project_collaborators",
    "list_workspace_groups",
)
HOLD_TOOLS = (
    "add_project_collaborator",
    "remove_project_collaborator",
    "set_project_collaborator_role",
    "add_workspace_group_member",
    "remove_workspace_group_member",
    "delete_project",
)
TOOL_NAMES = READ_TOOLS + HOLD_TOOLS

_ARGUMENTS: dict[str, tuple[tuple[str, str], ...]] = {
    "list_projects": (),
    "get_project": (("project_id", "Project id, for example proj_atlas."),),
    "search_projects": (("query", "Search text. The mock matches the project title."),),
    "list_project_collaborators": (("project_id", "Project id."),),
    "list_workspace_groups": (),
    "add_project_collaborator": (
        ("project_id", "Project id."),
        ("user_email", "Person to add."),
        ("role", "Role name such as viewer or admin."),
    ),
    "remove_project_collaborator": (
        ("project_id", "Project id."),
        ("user_email", "Person to remove."),
    ),
    "set_project_collaborator_role": (
        ("project_id", "Project id."),
        ("user_email", "Person whose role would change."),
        ("role", "Requested role, such as admin."),
    ),
    "add_workspace_group_member": (
        ("group_id", "Workspace group id."),
        ("user_email", "Person to add."),
    ),
    "remove_workspace_group_member": (
        ("group_id", "Workspace group id."),
        ("user_email", "Person to remove."),
    ),
    "delete_project": (("project_id", "Project id."),),
}


class MockMerlin:
    """In-memory Merlin stand-in. ``last_authorization`` is the raw header."""

    def __init__(self) -> None:
        self.last_authorization: str | None = None
        self.executed: list[str] = []
        self._lock = threading.Lock()

    def tool_definitions(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for name in TOOL_NAMES:
            properties: dict[str, Any] = {}
            required: list[str] = []
            for arg_name, description in _ARGUMENTS[name]:
                properties[arg_name] = {"type": "string", "description": description}
                required.append(arg_name)
            tools.append(
                {
                    "name": name,
                    "description": f"[mock] {name}. {MOCK_NOTICE}",
                    "inputSchema": {"type": "object", "properties": properties, "required": required},
                }
            )
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any], authorization: str | None) -> dict[str, Any]:
        with self._lock:
            self.last_authorization = authorization
            if name not in TOOL_NAMES:
                return _tool_error(f"[mock] unknown tool {name}. {MOCK_NOTICE}")
            self.executed.append(name)
        return _tool_ok(_payload(name, arguments))


def make_mock_server(mock: MockMerlin, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _handler(mock))


def _handler(mock: MockMerlin) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path not in {"/mcp", "/"}:
                self.send_error(404)
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
            authorization = self.headers.get("Authorization")
            response = _dispatch(mock, message, authorization)
            if response is None:
                body = b""
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    return Handler


def _dispatch(mock: MockMerlin, message: dict[str, Any], authorization: str | None) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "notifications/initialized":
        return None
    if not isinstance(method, str):
        return _error(request_id, -32600, "invalid request")
    if method == "initialize":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        version = params.get("protocolVersion") if isinstance(params, dict) else None
        protocol = version if isinstance(version, str) else "2024-11-05"
        return _result(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": MOCK_NAME, "version": "0"},
                "instructions": MOCK_NOTICE,
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": mock.tool_definitions()})
    if method == "tools/call":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments") if isinstance(params, dict) else {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error(request_id, -32602, "tools/call needs a name and an arguments object")
        return _result(request_id, mock.call_tool(name, arguments, authorization))
    if request_id is None:
        return None
    return _error(request_id, -32601, f"method not found: {method}")


def _payload(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    project = {
        "id": "proj_atlas",
        "title": "Atlas slides",
        "collaborators": [{"user_email": "owner@example.test", "role": "owner"}],
    }
    if name == "list_projects":
        body: dict[str, Any] = {"projects": [project]}
    elif name == "search_projects":
        body = {"projects": [project], "query": arguments.get("query", "")}
    elif name == "get_project":
        body = {"project": project}
    elif name == "list_project_collaborators":
        body = {"collaborators": project["collaborators"]}
    elif name == "list_workspace_groups":
        body = {"groups": [{"id": "grp_labelers", "name": "Labelers"}]}
    else:
        body = {"applied": False, "reason": "mock refused to change Encord state"}
    body["mock"] = True
    body["notice"] = MOCK_NOTICE
    return body


def _tool_ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}


def _tool_error(reason: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": reason}], "isError": True}


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
