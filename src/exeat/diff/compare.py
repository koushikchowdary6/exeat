# SPDX-License-Identifier: MIT
"""What an agent newly gains between two policies.

Role grants are compared after inheritance. Agent findings are only the
overrides (a lower ceiling, an exclusion, an extra deny). A new tool on a
role is reported once, naming the agents that inherit it.

``covers`` is conservative, so an unrecognized glob change is treated as a
widening. That can false-positive. It should not hide a real escalation.
"""

from __future__ import annotations

from dataclasses import dataclass

from exeat.money import format_usd
from exeat.policy.effective import effective_for_agent, effective_for_role
from exeat.policy.globmatch import covers
from exeat.policy.model import (
    ApprovalRule,
    Effective,
    Manifest,
    Policy,
    RateLimit,
    ScopeSet,
    SpendLimits,
    ToolSpec,
)

_SENSITIVE = frozenset({"path", "url", "table", "recipient", "spend"})
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "note": 3}
_FAIL_RANK = {"note": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    subject: str
    message: str


def diff_documents(
    base: Policy,
    head: Policy,
    base_manifest: Manifest,
    head_manifest: Manifest,
) -> list[Finding]:
    findings = [
        *_diff_roles(base, head),
        *_diff_agents(base, head),
        *_diff_manifest(base_manifest, head_manifest),
    ]
    return sorted(findings, key=lambda item: (_SEVERITY_RANK[item.severity], item.subject, item.code, item.message))


def should_fail(findings: list[Finding], fail_on: str) -> bool:
    if fail_on not in _FAIL_RANK:
        raise ValueError(f"unknown severity {fail_on}")
    threshold = _FAIL_RANK[fail_on]
    return any(_FAIL_RANK[item.severity] >= threshold for item in findings)


def _diff_roles(base: Policy, head: Policy) -> list[Finding]:
    findings: list[Finding] = []
    base_names = {role.name for role in base.roles}
    head_names = {role.name for role in head.roles}
    for name in sorted(head_names - base_names):
        effective = effective_for_role(head, name)
        bound = _inheritors(head, name)
        audience = f" Bound agents: {', '.join(bound)}." if bound else " No agent is bound to it."
        findings.append(
            Finding(
                "medium",
                "role.added",
                f"role {name}",
                f"New role {name}. Tools: {_names(effective.tools)}. Spend: {effective.spend.label()}.{audience}",
            )
        )
    for name in sorted(base_names - head_names):
        findings.append(Finding("note", "role.removed", f"role {name}", f"Role {name} was removed."))
    for name in sorted(base_names & head_names):
        before = effective_for_role(base, name)
        after = effective_for_role(head, name)
        findings.extend(_compare_role(head, before, after))
        old_parent = base.role(name).inherits
        new_parent = head.role(name).inherits
        if old_parent != new_parent:
            findings.append(
                Finding(
                    "medium",
                    "role.inherits",
                    f"role {name}",
                    f"Inheritance changed from {old_parent or 'nothing'} to {new_parent or 'nothing'}.",
                )
            )
    return findings


def _diff_agents(base: Policy, head: Policy) -> list[Finding]:
    findings: list[Finding] = []
    base_names = {agent.name for agent in base.agents}
    head_names = {agent.name for agent in head.agents}
    for name in sorted(head_names - base_names):
        effective = effective_for_agent(head, name)
        findings.append(
            Finding(
                "medium",
                "agent.added",
                f"agent {name}",
                f"New agent {name} on role {effective.role}. "
                f"Spend: {effective.spend.label()}. Tools: {_names(effective.tools)}.",
            )
        )
    for name in sorted(base_names - head_names):
        findings.append(Finding("note", "agent.removed", f"agent {name}", f"Agent {name} was removed."))
    for name in sorted(base_names & head_names):
        before_binding = base.agent(name).role
        after_binding = head.agent(name).role
        if before_binding != after_binding:
            findings.append(
                Finding(
                    "medium",
                    "agent.rebound",
                    f"agent {name}",
                    f"Rebound from role {before_binding} to {after_binding}.",
                )
            )
        before = effective_for_agent(base, name)
        after = effective_for_agent(head, name)
        before_role = effective_for_role(base, before_binding)
        after_role = effective_for_role(head, after_binding)
        findings.extend(_compare_override(before, after, before_role, after_role))
    return findings


def _compare_role(head: Policy, before: Effective, after: Effective) -> list[Finding]:
    subject = f"role {after.subject}"
    findings: list[Finding] = []
    inheritors = _inheritors(head, after.subject)
    for tool in sorted(after.tools - before.tools):
        held = [name for name in inheritors if tool in effective_for_agent(head, name).tools]
        excluded = [name for name in inheritors if name not in held]
        message = f"Gained tool {tool}."
        if held:
            message += f" Now held by {', '.join(held)}."
        if excluded:
            message += f" Excluded by {', '.join(excluded)}."
        findings.append(Finding("high", "tool.gained", subject, message))
    for tool in sorted(before.tools - after.tools):
        findings.append(Finding("note", "tool.removed", subject, f"Lost tool {tool}."))
    findings.extend(_spend_changes(head, before.spend, after.spend, subject, after.subject))
    findings.extend(_recipient_changes(before, after, subject))
    findings.extend(_scope_changes("file", before.files, after.files, subject))
    findings.extend(_scope_changes("url", before.urls, after.urls, subject))
    findings.extend(_scope_changes("table", before.tables, after.tables, subject))
    findings.extend(_approval_changes(before.approvals, after.approvals, subject))
    findings.extend(_rate_changes(before.rates, after.rates, subject))
    return findings


def _compare_override(
    before: Effective,
    after: Effective,
    before_role: Effective,
    after_role: Effective,
) -> list[Finding]:
    """Report only the part of an agent's power that the role diff does not already show."""

    subject = f"agent {after.subject}"
    findings: list[Finding] = []
    for field, label in (("per_call", "per-call"), ("per_day", "per-day"), ("total", "total")):
        agent_before = getattr(before.spend, field)
        agent_after = getattr(after.spend, field)
        role_before = getattr(before_role.spend, field)
        role_after = getattr(after_role.spend, field)
        personal = agent_before != role_before or agent_after != role_after
        if personal and agent_before != agent_after:
            findings.append(_ceiling_finding(subject, label, agent_before, agent_after, suffix=""))
    if before.tools != before_role.tools or after.tools != after_role.tools:
        role_gained = after_role.tools - before_role.tools
        role_lost = before_role.tools - after_role.tools
        extra_gained = (after.tools - before.tools) - role_gained
        extra_lost = (before.tools - after.tools) - role_lost
        for tool in sorted(extra_gained):
            findings.append(Finding("high", "tool.gained", subject, f"Gained tool {tool}."))
        for tool in sorted(extra_lost):
            findings.append(Finding("note", "tool.removed", subject, f"No longer has tool {tool}."))
    for kind, before_scope, after_scope, before_role_scope, after_role_scope in (
        ("file", before.files, after.files, before_role.files, after_role.files),
        ("url", before.urls, after.urls, before_role.urls, after_role.urls),
        ("table", before.tables, after.tables, before_role.tables, after_role.tables),
    ):
        personal_before = tuple(item for item in before_scope.deny if item not in before_role_scope.deny)
        personal_after = tuple(item for item in after_scope.deny if item not in after_role_scope.deny)
        for pattern in personal_after:
            if not any(covers(parent, pattern) for parent in personal_before):
                findings.append(
                    Finding(
                        "note",
                        f"{kind}.deny_added",
                        subject,
                        f"Added {kind} deny {pattern}.",
                    )
                )
        for pattern in personal_before:
            if not any(covers(parent, pattern) for parent in personal_after):
                findings.append(
                    Finding(
                        "high",
                        f"{kind}.deny_removed",
                        subject,
                        f"Removed {kind} deny {pattern}.",
                    )
                )
    return findings


def _rate_changes(before: tuple[RateLimit, ...], after: tuple[RateLimit, ...], subject: str) -> list[Finding]:
    before_map = {limit.name: limit for limit in before}
    after_map = {limit.name: limit for limit in after}
    findings: list[Finding] = []
    for name in sorted(set(before_map) - set(after_map)):
        findings.append(Finding("high", "rate.removed", subject, f"Removed rate limit {name}."))
    for name in sorted(set(after_map) - set(before_map)):
        limit = after_map[name]
        tools = ", ".join(sorted(limit.tools))
        findings.append(
            Finding(
                "note",
                "rate.added",
                subject,
                f"Added rate limit {name}: {limit.per_minute} calls per minute on {tools}.",
            )
        )
    for name in sorted(set(before_map) & set(after_map)):
        old = before_map[name]
        new = after_map[name]
        if new.per_minute > old.per_minute:
            findings.append(
                Finding(
                    "high",
                    "rate.loosened",
                    subject,
                    f"Rate limit {name} rose from {old.per_minute} to {new.per_minute} calls per minute.",
                )
            )
        elif new.per_minute < old.per_minute:
            findings.append(
                Finding(
                    "note",
                    "rate.tightened",
                    subject,
                    f"Rate limit {name} fell from {old.per_minute} to {new.per_minute} calls per minute.",
                )
            )
        dropped = sorted(old.tools - new.tools)
        added = sorted(new.tools - old.tools)
        if dropped:
            findings.append(
                Finding(
                    "high",
                    "rate.uncovered",
                    subject,
                    f"Rate limit {name} no longer covers {', '.join(dropped)}.",
                )
            )
        if added:
            findings.append(
                Finding(
                    "note",
                    "rate.covered",
                    subject,
                    f"Rate limit {name} now covers {', '.join(added)}.",
                )
            )
    return findings


def _spend_changes(
    head: Policy,
    before: SpendLimits,
    after: SpendLimits,
    subject: str,
    role_name: str,
) -> list[Finding]:
    findings: list[Finding] = []
    for field, label in (("per_call", "per-call"), ("per_day", "per-day"), ("total", "total")):
        old = getattr(before, field)
        new = getattr(after, field)
        if old == new:
            continue
        suffix = ""
        if isinstance(new, int):
            lower = _lower_caps(head, role_name, field, new)
            if lower:
                suffix = f" Still capped lower: {lower}."
        findings.append(_ceiling_finding(subject, label, old, new, suffix=suffix))
    return findings


def _ceiling_finding(
    subject: str,
    label: str,
    before: int | None,
    after: int | None,
    *,
    suffix: str,
) -> Finding:
    if before is None and after is not None:
        return Finding(
            "high",
            "spend.granted",
            subject,
            f"Gained a {label} ceiling of {format_usd(after)} (spend on this axis was denied before).{suffix}",
        )
    if after is None and before is not None:
        return Finding(
            "note",
            "spend.removed",
            subject,
            f"Removed the {label} ceiling of {format_usd(before)}. Spend on this axis is now denied.",
        )
    assert before is not None and after is not None
    if after > before:
        return Finding(
            "high",
            "spend.raised",
            subject,
            f"Raised the {label} ceiling from {format_usd(before)} to {format_usd(after)}.{suffix}",
        )
    return Finding(
        "note",
        "spend.lowered",
        subject,
        f"Lowered the {label} ceiling from {format_usd(before)} to {format_usd(after)}.",
    )


def _lower_caps(policy: Policy, role_name: str, field: str, role_ceiling: int) -> str:
    parts: list[str] = []
    for agent in policy.agents:
        effective = effective_for_agent(policy, agent.name)
        if role_name not in effective.role_chain:
            continue
        value = getattr(effective.spend, field)
        if isinstance(value, int) and value < role_ceiling:
            parts.append(f"{agent.name} at {format_usd(value)}")
    return ", ".join(parts)


def _recipient_changes(before: Effective, after: Effective, subject: str) -> list[Finding]:
    findings: list[Finding] = []
    for domain in sorted(after.recipient_domains - before.recipient_domains):
        findings.append(Finding("high", "recipient.domain_added", subject, f"New recipient domain {domain}."))
    for domain in sorted(before.recipient_domains - after.recipient_domains):
        findings.append(
            Finding(
                "note",
                "recipient.domain_removed",
                subject,
                f"Removed recipient domain {domain}.",
            )
        )
    for address in sorted(after.recipient_addresses - before.recipient_addresses):
        findings.append(
            Finding(
                "medium",
                "recipient.address_added",
                subject,
                f"New recipient address {address}.",
            )
        )
    for address in sorted(before.recipient_addresses - after.recipient_addresses):
        findings.append(
            Finding(
                "note",
                "recipient.address_removed",
                subject,
                f"Removed recipient address {address}.",
            )
        )
    return findings


def _scope_changes(kind: str, before: ScopeSet, after: ScopeSet, subject: str) -> list[Finding]:
    findings: list[Finding] = []
    for pattern in after.allow:
        if not _covered(pattern, before.allow):
            findings.append(
                Finding(
                    "high",
                    f"{kind}.allow_widened",
                    subject,
                    f"Widened {kind} allow with {pattern}.",
                )
            )
    for pattern in before.allow:
        if not _covered(pattern, after.allow):
            findings.append(
                Finding(
                    "note",
                    f"{kind}.allow_narrowed",
                    subject,
                    f"Narrowed {kind} allow by dropping {pattern}.",
                )
            )
    for pattern in before.deny:
        if not _covered(pattern, after.deny):
            findings.append(
                Finding(
                    "high",
                    f"{kind}.deny_removed",
                    subject,
                    f"Removed {kind} deny {pattern}.",
                )
            )
    for pattern in after.deny:
        if not _covered(pattern, before.deny):
            findings.append(Finding("note", f"{kind}.deny_added", subject, f"Added {kind} deny {pattern}."))
    return findings


def _covered(pattern: str, patterns: tuple[str, ...]) -> bool:
    return any(covers(parent, pattern) for parent in patterns)


def _approval_changes(
    before: tuple[ApprovalRule, ...],
    after: tuple[ApprovalRule, ...],
    subject: str,
) -> list[Finding]:
    old = {rule.name: rule for rule in before}
    new = {rule.name: rule for rule in after}
    findings: list[Finding] = []
    for name in sorted(set(old) - set(new)):
        findings.append(
            Finding(
                "high",
                "approval.removed",
                subject,
                f"Removed approval {name} ({old[name].label()}).",
            )
        )
    for name in sorted(set(new) - set(old)):
        findings.append(
            Finding(
                "note",
                "approval.added",
                subject,
                f"Added approval {name} ({new[name].label()}).",
            )
        )
    for name in sorted(set(old) & set(new)):
        findings.extend(_approval_delta(old[name], new[name], subject))
    return findings


def _approval_delta(before: ApprovalRule, after: ApprovalRule, subject: str) -> list[Finding]:
    findings: list[Finding] = []
    if before.always and not after.always:
        findings.append(
            Finding(
                "high",
                "approval.loosened",
                subject,
                f"Approval {after.name} is no longer always required.",
            )
        )
    elif after.always and not before.always:
        findings.append(
            Finding(
                "note",
                "approval.tightened",
                subject,
                f"Approval {after.name} is now always required.",
            )
        )
    if before.gt_cents is not None and after.gt_cents is not None and before.gt_cents != after.gt_cents:
        if after.gt_cents > before.gt_cents:
            findings.append(
                Finding(
                    "high",
                    "approval.loosened",
                    subject,
                    f"Approval {after.name} threshold rose from {format_usd(before.gt_cents)} "
                    f"to {format_usd(after.gt_cents)} (fewer calls wait for a person).",
                )
            )
        else:
            findings.append(
                Finding(
                    "note",
                    "approval.tightened",
                    subject,
                    f"Approval {after.name} threshold fell from {format_usd(before.gt_cents)} "
                    f"to {format_usd(after.gt_cents)}.",
                )
            )
    for tool in sorted(before.tools - after.tools):
        findings.append(
            Finding(
                "high",
                "approval.loosened",
                subject,
                f"Approval {after.name} no longer covers {tool}.",
            )
        )
    for tool in sorted(after.tools - before.tools):
        findings.append(
            Finding(
                "note",
                "approval.tightened",
                subject,
                f"Approval {after.name} now also covers {tool}.",
            )
        )
    return findings


def _diff_manifest(base: Manifest, head: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    old = {tool.name: tool for tool in base.tools}
    new = {tool.name: tool for tool in head.tools}
    for name in sorted(set(new) - set(old)):
        findings.append(
            Finding(
                "low",
                "manifest.tool_added",
                "manifest",
                f"Manifest declares {name}. It does nothing until a role grants it.",
            )
        )
    for name in sorted(set(old) - set(new)):
        findings.append(
            Finding(
                "note",
                "manifest.tool_removed",
                "manifest",
                f"Manifest no longer declares {name}.",
            )
        )
    for name in sorted(set(old) & set(new)):
        findings.extend(_diff_tool(old[name], new[name]))
    return findings


def _diff_tool(before: ToolSpec, after: ToolSpec) -> list[Finding]:
    findings: list[Finding] = []
    old = {arg.name: arg for arg in before.arguments}
    new = {arg.name: arg for arg in after.arguments}
    for name, arg in old.items():
        if name not in new:
            if arg.type in _SENSITIVE:
                findings.append(
                    Finding(
                        "high",
                        "manifest.unclassified",
                        "manifest",
                        f"{before.name} argument {name} ({arg.type}) was removed from the manifest.",
                    )
                )
            else:
                findings.append(
                    Finding(
                        "note",
                        "manifest.arg_removed",
                        "manifest",
                        f"{before.name} argument {name} was removed.",
                    )
                )
            continue
        updated = new[name]
        if arg.type == updated.type:
            continue
        if arg.type in _SENSITIVE and updated.type not in _SENSITIVE:
            findings.append(
                Finding(
                    "high",
                    "manifest.downgraded",
                    "manifest",
                    f"{before.name} argument {name} changed from {arg.type} to {updated.type}. "
                    "Reserved names are still enforced at runtime, but the manifest no longer "
                    "tells reviewers the truth.",
                )
            )
        elif updated.type in _SENSITIVE and arg.type not in _SENSITIVE:
            findings.append(
                Finding(
                    "note",
                    "manifest.upgraded",
                    "manifest",
                    f"{before.name} argument {name} is now classified as {updated.type}.",
                )
            )
        else:
            findings.append(
                Finding(
                    "medium",
                    "manifest.reclassified",
                    "manifest",
                    f"{before.name} argument {name} changed from {arg.type} to {updated.type}.",
                )
            )
    for name, arg in new.items():
        if name not in old and arg.type in _SENSITIVE:
            findings.append(
                Finding(
                    "note",
                    "manifest.classified",
                    "manifest",
                    f"{after.name} argument {name} is classified as {arg.type}.",
                )
            )
    return findings


def _inheritors(policy: Policy, role_name: str) -> list[str]:
    names: list[str] = []
    for agent in policy.agents:
        effective = effective_for_agent(policy, agent.name)
        if role_name in effective.role_chain:
            names.append(agent.name)
    return names


def _names(tools: frozenset[str]) -> str:
    if not tools:
        return "none"
    return ", ".join(sorted(tools))
