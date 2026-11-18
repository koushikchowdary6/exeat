# SPDX-License-Identifier: MIT
"""The three answers the gate can give."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentleash.errors import AgentleashError


class Effect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class Check:
    """One step of an explain trace. ``status`` is pass, fail, or hold."""

    status: str
    rule: str
    detail: str


@dataclass(frozen=True)
class Decision:
    effect: Effect
    rule: str
    reason: str
    agent: str
    role: str
    tool: str
    fingerprint: str
    policy_hash: str
    spend_cents: int | None = None
    approval_id: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    checks: tuple[Check, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.effect is Effect.ALLOW


class Denied(AgentleashError):
    """Raised by the decorator when the gate denies a call."""

    def __init__(self, decision: Decision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


class ApprovalRequired(AgentleashError):
    """Raised by the decorator when a person has to approve the call."""

    def __init__(self, decision: Decision) -> None:
        super().__init__(decision.reason)
        self.decision = decision
