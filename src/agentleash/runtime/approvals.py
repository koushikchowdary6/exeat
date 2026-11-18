# SPDX-License-Identifier: Apache-2.0
"""Human approvals. Granting and consuming are both append-only events."""

from __future__ import annotations

import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any

from agentleash.canon import fingerprint
from agentleash.jsonl import append_jsonl, read_jsonl
from agentleash.runtime.clock import Clock, SystemClock, isoformat

DEFAULT_TTL_SECONDS = 30 * 60


class ApprovalLog:
    def __init__(self, path: Path, clock: Clock | None = None) -> None:
        self.path = path
        self.clock = clock or SystemClock()

    def read(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path)

    def grant(
        self,
        *,
        agent: str,
        tool: str,
        arguments: dict[str, Any],
        by: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> dict[str, Any]:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = self.clock.now()
        record: dict[str, Any] = {
            "v": 1,
            "type": "approval",
            "id": f"apr_{secrets.token_hex(6)}",
            "ts": isoformat(now),
            "expires": isoformat(now + timedelta(seconds=ttl_seconds)),
            "agent": agent,
            "tool": tool,
            "fingerprint": fingerprint(agent, tool, arguments),
            "by": by,
        }
        append_jsonl(self.path, record)
        return record

    def consume(self, approval_id: str, *, audit_id: str) -> None:
        append_jsonl(
            self.path,
            {
                "v": 1,
                "type": "consumed",
                "approval_id": approval_id,
                "audit_id": audit_id,
                "ts": isoformat(self.clock.now()),
            },
        )
