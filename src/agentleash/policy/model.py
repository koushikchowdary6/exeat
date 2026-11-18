# SPDX-License-Identifier: MIT
"""In-memory policy and tool manifest."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agentleash.money import format_usd


@dataclass(frozen=True)
class SpendLimits:
    """Ceilings in cents. ``None`` means that axis was not set (spend denied)."""

    per_call: int | None = None
    per_day: int | None = None
    total: int | None = None
    currency: str = "USD"

    def is_set(self) -> bool:
        return self.per_call is not None or self.per_day is not None or self.total is not None

    def label(self) -> str:
        if not self.is_set():
            return "no spend ceiling (spend denied)"
        return f"{format_usd(self.per_call)} / call · {format_usd(self.per_day)} / day · {format_usd(self.total)} total"


@dataclass(frozen=True)
class ScopeSet:
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()

    def union(self, other: ScopeSet) -> ScopeSet:
        return ScopeSet(
            allow=_dedupe(self.allow + other.allow),
            deny=_dedupe(self.deny + other.deny),
        )


@dataclass(frozen=True)
class ApprovalRule:
    """Hold a call for a person. A hold never raises a spend ceiling."""

    name: str
    tools: frozenset[str]
    reason: str
    always: bool = False
    arg: str | None = None
    gt_cents: int | None = None

    def label(self) -> str:
        if self.always:
            watched = ", ".join(sorted(self.tools))
            return f"{self.name}: always on {watched}"
        assert self.arg is not None and self.gt_cents is not None
        watched = ", ".join(sorted(self.tools))
        return f"{self.name}: {self.arg} > {format_usd(self.gt_cents)} on {watched}"


@dataclass(frozen=True)
class Role:
    name: str
    description: str
    inherits: str | None
    tools: tuple[str, ...]
    spend: SpendLimits
    recipient_domains: tuple[str, ...]
    recipient_addresses: tuple[str, ...]
    files: ScopeSet
    urls: ScopeSet
    tables: ScopeSet
    approvals: tuple[ApprovalRule, ...]


@dataclass(frozen=True)
class Agent:
    name: str
    role: str
    description: str
    spend: SpendLimits
    exclude_tools: frozenset[str]
    files: ScopeSet
    urls: ScopeSet
    tables: ScopeSet


@dataclass(frozen=True)
class Org:
    name: str
    unit: str


@dataclass(frozen=True)
class Policy:
    version: int
    org: Org
    roles: tuple[Role, ...]
    agents: tuple[Agent, ...]

    def role(self, name: str) -> Role:
        for role in self.roles:
            if role.name == name:
                return role
        raise KeyError(name)

    def agent(self, name: str) -> Agent:
        for agent in self.agents:
            if agent.name == name:
                return agent
        raise KeyError(name)

    def content_hash(self) -> str:
        payload = json.dumps(_plain_policy(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Argument:
    name: str
    type: str
    required: bool
    description: str = ""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: str
    arguments: tuple[Argument, ...]

    def argument(self, name: str) -> Argument | None:
        for argument in self.arguments:
            if argument.name == name:
                return argument
        return None


@dataclass(frozen=True)
class Manifest:
    version: int
    tools: tuple[ToolSpec, ...]

    def tool(self, name: str) -> ToolSpec | None:
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    def content_hash(self) -> str:
        payload = json.dumps(_plain_manifest(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Effective:
    """What one agent, or one role before agent narrowing, can actually do."""

    subject: str
    kind: str  # "agent" or "role"
    role: str
    role_chain: tuple[str, ...]
    description: str
    tools: frozenset[str]
    spend: SpendLimits
    recipient_domains: frozenset[str]
    recipient_addresses: frozenset[str]
    files: ScopeSet
    urls: ScopeSet
    tables: ScopeSet
    approvals: tuple[ApprovalRule, ...]


def _dedupe(items: tuple[str, ...]) -> tuple[str, ...]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def _plain_policy(policy: Policy) -> dict[str, object]:
    return {
        "version": policy.version,
        "org": {"name": policy.org.name, "unit": policy.org.unit},
        "roles": [
            {
                "name": role.name,
                "description": role.description,
                "inherits": role.inherits,
                "tools": list(role.tools),
                "spend": _plain_spend(role.spend),
                "recipient_domains": list(role.recipient_domains),
                "recipient_addresses": list(role.recipient_addresses),
                "files": {"allow": list(role.files.allow), "deny": list(role.files.deny)},
                "urls": {"allow": list(role.urls.allow), "deny": list(role.urls.deny)},
                "tables": {"allow": list(role.tables.allow), "deny": list(role.tables.deny)},
                "approvals": [_plain_approval(rule) for rule in role.approvals],
            }
            for role in policy.roles
        ],
        "agents": [
            {
                "name": agent.name,
                "role": agent.role,
                "description": agent.description,
                "spend": _plain_spend(agent.spend),
                "exclude_tools": sorted(agent.exclude_tools),
                "files": {"allow": list(agent.files.allow), "deny": list(agent.files.deny)},
                "urls": {"allow": list(agent.urls.allow), "deny": list(agent.urls.deny)},
                "tables": {"allow": list(agent.tables.allow), "deny": list(agent.tables.deny)},
            }
            for agent in policy.agents
        ],
    }


def _plain_spend(spend: SpendLimits) -> dict[str, object]:
    return {
        "per_call": spend.per_call,
        "per_day": spend.per_day,
        "total": spend.total,
        "currency": spend.currency,
    }


def _plain_approval(rule: ApprovalRule) -> dict[str, object]:
    return {
        "name": rule.name,
        "tools": sorted(rule.tools),
        "reason": rule.reason,
        "always": rule.always,
        "arg": rule.arg,
        "gt_cents": rule.gt_cents,
    }


def _plain_manifest(manifest: Manifest) -> dict[str, object]:
    return {
        "version": manifest.version,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "risk": tool.risk,
                "arguments": [
                    {
                        "name": arg.name,
                        "type": arg.type,
                        "required": arg.required,
                        "description": arg.description,
                    }
                    for arg in tool.arguments
                ],
            }
            for tool in manifest.tools
        ],
    }
