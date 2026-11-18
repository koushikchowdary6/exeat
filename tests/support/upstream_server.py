# SPDX-License-Identifier: MIT
"""Tiny upstream MCP server for the proxy test. It records every tools/call."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    log = Path(os.environ["UPSTREAM_LOG"])
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        message = json.loads(line)
        method = message.get("method")
        if method == "notifications/initialized":
            continue
        request_id = message.get("id")
        if method == "initialize":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "upstream-fixture", "version": "0"},
                    },
                }
            )
        elif method == "tools/list":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {"name": "ledger.read", "description": "read"},
                            {"name": "shell.exec", "description": "shell"},
                        ]
                    },
                }
            )
        elif method == "tools/call":
            params = message.get("params") or {}
            name = str(params.get("name", ""))
            with log.open("a", encoding="utf-8") as handle:
                handle.write(name + "\n")
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": f"ok:{name}"}],
                        "isError": False,
                    },
                }
            )
        elif request_id is not None:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "nope"},
                }
            )


def _send(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
