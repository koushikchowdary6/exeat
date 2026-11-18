# SPDX-License-Identifier: MIT
"""Tiny MCP client for Exeat's HTTP/SSE gate. Tests and the Merlin demo use it."""

from __future__ import annotations

import json
import queue
import threading
import urllib.request
from typing import Any


class SseClientError(RuntimeError):
    """The local SSE client could not finish a JSON-RPC exchange."""


def exchange(
    base_url: str,
    messages: list[dict[str, Any]],
    *,
    authorization: str | None,
    timeout: float = 5,
) -> list[dict[str, Any]]:
    """Open ``GET /sse``, post each message, and return the JSON-RPC replies in order."""

    events: queue.Queue[tuple[str, str]] = queue.Queue()
    errors: list[BaseException] = []

    def _read() -> None:
        request = urllib.request.Request(base_url.rstrip("/") + "/sse", method="GET")
        if authorization is not None:
            request.add_header("Authorization", authorization)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                event_name = "message"
                data_lines: list[str] = []
                while True:
                    raw = response.readline()
                    if raw == b"":
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line == "":
                        if data_lines:
                            events.put((event_name, "\n".join(data_lines)))
                        event_name = "message"
                        data_lines = []
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip() or "message"
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
        except BaseException as exc:
            errors.append(exc)
            events.put(("error", str(exc)))

    thread = threading.Thread(target=_read, name="exeat-sse-client", daemon=True)
    thread.start()
    try:
        kind, data = events.get(timeout=timeout)
    except queue.Empty as exc:
        raise SseClientError("timed out waiting for the SSE endpoint") from exc
    if kind != "endpoint":
        detail = errors[0] if errors else data
        raise SseClientError(f"expected an endpoint event, got {kind}: {detail}")
    endpoint = data.strip()
    replies: list[dict[str, Any]] = []
    for message in messages:
        _post(base_url, endpoint, message, authorization, timeout)
        if "id" not in message:
            continue
        kind, data = _next_message(events, timeout)
        if kind != "message":
            raise SseClientError(f"expected a message event, got {kind}")
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise SseClientError("SSE message was not a JSON object")
        replies.append(parsed)
    return replies


def _post(base_url: str, endpoint: str, message: dict[str, Any], authorization: str | None, timeout: float) -> None:
    absolute = endpoint.startswith(("http://", "https://"))
    target = endpoint if absolute else base_url.rstrip("/") + endpoint
    request = urllib.request.Request(
        target,
        data=json.dumps(message).encode("utf-8"),
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    if authorization is not None:
        request.add_header("Authorization", authorization)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def _next_message(events: queue.Queue[tuple[str, str]], timeout: float) -> tuple[str, str]:
    try:
        return events.get(timeout=timeout)
    except queue.Empty as exc:
        raise SseClientError("timed out waiting for an SSE message") from exc
