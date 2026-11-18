# SPDX-License-Identifier: MIT
"""Resolve inheritance. Agents can only narrow what a role already grants."""

from __future__ import annotations

from exeat.errors import PolicyError
from exeat.policy.model import (
    Agent,
    Effective,
    Policy,
    Role,
    ScopeSet,
    SpendLimits,
)


def effective_for_agent(policy: Policy, agent_name: str) -> Effective:
    try:
        agent = policy.agent(agent_name)
    except KeyError as exc:
        raise PolicyError(f"unknown agent {agent_name}") from exc
    role_effective = effective_for_role(policy, agent.role)
    return _narrow(role_effective, agent)


def effective_for_role(policy: Policy, role_name: str) -> Effective:
    chain = _chain(policy, role_name)
    merged = _empty_role(chain[0])
    for role in chain:
        merged = _merge_role(merged, role)
    return merged


def _chain(policy: Policy, role_name: str) -> tuple[Role, ...]:
    try:
        role = policy.role(role_name)
    except KeyError as exc:
        raise PolicyError(f"unknown role {role_name}") from exc
    names: list[str] = []
    seen: set[str] = set()
    current: Role | None = role
    while current is not None:
        if current.name in seen:
            raise PolicyError(f"role inheritance cycle involving {current.name}")
        seen.add(current.name)
        names.append(current.name)
        if current.inherits is None:
            break
        try:
            current = policy.role(current.inherits)
        except KeyError as exc:
            raise PolicyError(f"role {names[-1]} inherits unknown role {current.inherits}") from exc
    names.reverse()
    return tuple(policy.role(name) for name in names)


def _dedupe(items: tuple[str, ...]) -> tuple[str, ...]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def _empty_role(role: Role) -> Effective:
    return Effective(
        subject=role.name,
        kind="role",
        role=role.name,
        role_chain=(role.name,),
        description=role.description,
        tools=frozenset(),
        spend=SpendLimits(),
        recipient_domains=frozenset(),
        recipient_addresses=frozenset(),
        files=ScopeSet(),
        urls=ScopeSet(),
        tables=ScopeSet(),
        approvals=(),
    )


def _merge_role(parent: Effective, role: Role) -> Effective:
    """Child roles add grants. They cannot delete a parent deny or a parent hold."""

    approvals = list(parent.approvals)
    by_name = {rule.name: index for index, rule in enumerate(approvals)}
    for rule in role.approvals:
        if rule.name in by_name:
            # A child may replace the threshold. The diff flags a looser one.
            approvals[by_name[rule.name]] = rule
        else:
            approvals.append(rule)
    return Effective(
        subject=role.name,
        kind="role",
        role=role.name,
        role_chain=parent.role_chain + ((role.name,) if parent.role != role.name else ()),
        description=role.description or parent.description,
        tools=parent.tools | frozenset(role.tools),
        spend=_override_spend(parent.spend, role.spend),
        recipient_domains=parent.recipient_domains | frozenset(role.recipient_domains),
        recipient_addresses=parent.recipient_addresses | frozenset(role.recipient_addresses),
        files=parent.files.union(role.files),
        urls=parent.urls.union(role.urls),
        tables=parent.tables.union(role.tables),
        approvals=tuple(approvals),
    )


def _override_spend(parent: SpendLimits, child: SpendLimits) -> SpendLimits:
    return SpendLimits(
        per_call=child.per_call if child.per_call is not None else parent.per_call,
        per_day=child.per_day if child.per_day is not None else parent.per_day,
        total=child.total if child.total is not None else parent.total,
        currency=child.currency or parent.currency,
    )


def _narrow(role_effective: Effective, agent: Agent) -> Effective:
    missing = agent.exclude_tools - role_effective.tools
    if missing:
        names = ", ".join(sorted(missing))
        raise PolicyError(f"agent {agent.name} excludes {names}, which role {agent.role} does not grant")
    if agent.files.allow or agent.urls.allow or agent.tables.allow:
        raise PolicyError(f"agent {agent.name} cannot add allow patterns")
    return Effective(
        subject=agent.name,
        kind="agent",
        role=agent.role,
        role_chain=role_effective.role_chain,
        description=agent.description,
        tools=role_effective.tools - agent.exclude_tools,
        spend=_tighten_spend(role_effective.spend, agent),
        recipient_domains=role_effective.recipient_domains,
        recipient_addresses=role_effective.recipient_addresses,
        files=ScopeSet(
            allow=role_effective.files.allow,
            deny=_dedupe(role_effective.files.deny + agent.files.deny),
        ),
        urls=ScopeSet(
            allow=role_effective.urls.allow,
            deny=_dedupe(role_effective.urls.deny + agent.urls.deny),
        ),
        tables=ScopeSet(
            allow=role_effective.tables.allow,
            deny=_dedupe(role_effective.tables.deny + agent.tables.deny),
        ),
        approvals=role_effective.approvals,
    )


def _tighten_spend(role_spend: SpendLimits, agent: Agent) -> SpendLimits:
    """Keep the lower ceiling. An agent cannot invent or raise one."""

    tightened: dict[str, int | None] = {}
    for field in ("per_call", "per_day", "total"):
        role_value: int | None = getattr(role_spend, field)
        agent_value: int | None = getattr(agent.spend, field)
        if agent_value is None:
            tightened[field] = role_value
            continue
        if role_value is None:
            raise PolicyError(
                f"agent {agent.name} sets {field} but role {agent.role} has no {field} ceiling. "
                "Agents cannot grant spend a role does not have."
            )
        if agent_value > role_value:
            raise PolicyError(
                f"agent {agent.name} {field} is higher than role {agent.role}. Agents can only lower a ceiling."
            )
        tightened[field] = agent_value
    return SpendLimits(
        per_call=tightened["per_call"],
        per_day=tightened["per_day"],
        total=tightened["total"],
        currency=role_spend.currency,
    )
