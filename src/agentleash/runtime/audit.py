# SPDX-License-Identifier: Apache-2.0
"""The decision tape. Append-only; a failed tool adds a release line."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from agentleash.brand import PRODUCT_SLUG, VERSION
from agentleash.canon import truncate
from agentleash.decision import Decision, Effect
from agentleash.jsonl import append_jsonl, read_jsonl
from agentleash.runtime.clock import Clock, SystemClock, isoformat


class AuditLog:
    def __init__(self, path: Path, clock: Clock | None = None) -> None:
        self.path = path
        self.clock = clock or SystemClock()

    def read(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path)

    def record(self, decision: Decision, *, commit: bool) -> str:
        audit_id = _new_id("dec")
        effect = decision.effect.value
        append_jsonl(
            self.path,
            {
                "v": 1,
                "id": audit_id,
                "ts": isoformat(self.clock.now()),
                "effect": effect,
                "committed": bool(commit and decision.effect is Effect.ALLOW),
                "agent": decision.agent,
                "role": decision.role,
                "tool": decision.tool,
                "rule": decision.rule,
                "reason": decision.reason,
                "arguments": truncate(decision.arguments),
                "fingerprint": decision.fingerprint,
                "policy_hash": decision.policy_hash,
                "spend_cents": decision.spend_cents or 0,
                "approval_id": decision.approval_id,
                "product": PRODUCT_SLUG,
                "product_version": VERSION,
            },
        )
        return audit_id

    def release(self, audit_id: str, *, agent: str, tool: str, reason: str) -> str:
        release_id = _new_id("rel")
        append_jsonl(
            self.path,
            {
                "v": 1,
                "id": release_id,
                "ts": isoformat(self.clock.now()),
                "effect": "release",
                "committed": False,
                "reverses": audit_id,
                "agent": agent,
                "tool": tool,
                "reason": reason,
                "product": PRODUCT_SLUG,
                "product_version": VERSION,
            },
        )
        return release_id


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"
