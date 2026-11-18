# SPDX-License-Identifier: MIT
"""A plain-text org chart of roles, agents, and ceilings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from exeat.money import format_usd
from exeat.policy.effective import effective_for_agent, effective_for_role
from exeat.policy.model import Effective, Policy
from exeat.spend import committed_spend


def render_chart(
    policy: Policy,
    *,
    history: Sequence[Mapping[str, Any]] = (),
    now: datetime | None = None,
) -> str:
    moment = now or datetime.now(UTC)
    lines = [
        f"{policy.org.name}" + (f"  ·  {policy.org.unit}" if policy.org.unit else ""),
        f"policy {policy.content_hash()}  ·  deny by default",
        "",
    ]
    roots = [role.name for role in policy.roles if role.inherits is None]
    for name in roots:
        lines.extend(_role_lines(policy, name, history, moment, depth=0))
    orphans = [agent.name for agent in policy.agents if agent.role not in {role.name for role in policy.roles}]
    for name in orphans:
        lines.append(f"(unbound agent {name})")
    return "\n".join(lines).rstrip() + "\n"


def _role_lines(
    policy: Policy,
    name: str,
    history: Sequence[Mapping[str, Any]],
    now: datetime,
    *,
    depth: int,
) -> list[str]:
    effective = effective_for_role(policy, name)
    pad = "  " * depth
    lines = [f"{pad}{name}", *_detail_lines(effective, pad + "  ", inherited=depth > 0)]
    for agent in policy.agents:
        if agent.role != name:
            continue
        agent_effective = effective_for_agent(policy, agent.name)
        lines.append(f"{pad}  └── {agent.name}")
        lines.extend(_agent_lines(agent_effective, history, now, pad + "      "))
    for child in policy.roles:
        if child.inherits == name:
            lines.extend(_role_lines(policy, child.name, history, now, depth=depth + 1))
    return lines


def _agent_lines(
    effective: Effective,
    history: Sequence[Mapping[str, Any]],
    now: datetime,
    pad: str,
) -> list[str]:
    lines = []
    if effective.description:
        lines.append(f"{pad}{effective.description}")
    today, total = committed_spend(history, agent=effective.subject, now=now)
    if effective.spend.is_set():
        lines.append(f"{pad}ceilings  {effective.spend.label()}")
        lines.append(
            f"{pad}used      today {format_usd(today)} of {format_usd(effective.spend.per_day)}"
            f"  ·  total {format_usd(total)} of {format_usd(effective.spend.total)}"
        )
    return lines


def _detail_lines(effective: Effective, pad: str, *, inherited: bool) -> list[str]:
    lines: list[str] = []
    if effective.description:
        lines.append(f"{pad}{effective.description}")
    tools = ", ".join(sorted(effective.tools)) or "none"
    label = "tools" if not inherited else "tools (with parent)"
    lines.append(f"{pad}{label:<16}  {tools}")
    if effective.spend.is_set():
        lines.append(f"{pad}{'spend':<16}  {effective.spend.label()}")
    if effective.recipient_domains or effective.recipient_addresses:
        domains = " ".join(f"*@{item}" for item in sorted(effective.recipient_domains))
        addresses = " ".join(sorted(effective.recipient_addresses))
        lines.append(f"{pad}{'mail':<16}  {domains}  {addresses}".rstrip())
    if effective.files.allow:
        lines.append(f"{pad}{'files':<16}  {', '.join(effective.files.allow)}")
    if effective.files.deny:
        lines.append(f"{pad}{'files deny':<16}  {', '.join(effective.files.deny)}")
    if effective.tables.allow:
        lines.append(f"{pad}{'tables':<16}  {', '.join(effective.tables.allow)}")
    if effective.urls.allow:
        lines.append(f"{pad}{'urls':<16}  {', '.join(effective.urls.allow)}")
    for rule in effective.approvals:
        lines.append(f"{pad}{'hold':<16}  {rule.label()}")
    return lines
