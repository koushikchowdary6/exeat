# SPDX-License-Identifier: MIT
"""Decide allow, deny, or hold. Deny wins, and a hold never raises a ceiling.

Check order:
1. The agent exists.
2. The tool is declared in the manifest.
3. The tool is granted to the agent.
4. Required arguments are present, and reserved names keep their sensitive type.
5. Paths, URLs, tables, and recipients are inside scope. Protected paths always lose.
6. Spend ceilings: per call, then UTC day, then total. Unset ceilings deny.
7. Per-tool rate limits, counted from the audit tape over the last minute.
8. Approval rules. A matching unconsumed approval turns a hold into an allow.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from exeat.brand import RESERVED_ARGUMENT_KINDS
from exeat.canon import canon, fingerprint
from exeat.decision import Check, Decision, Effect
from exeat.errors import PolicyError
from exeat.money import format_usd, parse_cents
from exeat.policy.check import (
    Classified,
    path_problem,
    recipient_problem,
    spend_cents,
    table_problem,
    url_problem,
)
from exeat.policy.effective import effective_for_agent
from exeat.policy.model import ApprovalRule, Effective, Manifest, Policy, ToolSpec
from exeat.spend import committed_spend

_SENSITIVE = frozenset({"path", "url", "table", "recipient", "spend"})


def evaluate(
    policy: Policy,
    manifest: Manifest,
    agent_name: str,
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    history: Sequence[Mapping[str, Any]] = (),
    approvals: Sequence[Mapping[str, Any]] = (),
    now: datetime | None = None,
    policy_hash: str | None = None,
    spent: tuple[int, int] | None = None,
) -> Decision:
    moment = now.astimezone(UTC) if now else datetime.now(UTC)
    digest = policy_hash or policy.content_hash()
    if not isinstance(arguments, Mapping):
        invalid = Check("fail", "args.invalid", "Denied: tool arguments must be an object.")
        return Decision(
            effect=Effect.DENY,
            rule="args.invalid",
            reason=invalid.detail,
            agent=agent_name,
            role="",
            tool=tool_name,
            fingerprint="",
            policy_hash=digest,
            checks=(invalid,),
        )
    args = dict(arguments)
    token = fingerprint(agent_name, tool_name, args)
    checks: list[Check] = []

    def finish(
        effect: Effect,
        rule: str,
        reason: str,
        *,
        role: str = "",
        spend: int | None = None,
        approval_id: str | None = None,
    ) -> Decision:
        status = "pass" if effect is Effect.ALLOW else "hold" if effect is Effect.REQUIRE_APPROVAL else "fail"
        checks.append(Check(status, rule, reason))
        return Decision(
            effect=effect,
            rule=rule,
            reason=reason,
            agent=agent_name,
            role=role,
            tool=tool_name,
            fingerprint=token,
            policy_hash=digest,
            spend_cents=spend,
            approval_id=approval_id,
            arguments=canon(args),
            checks=tuple(checks),
        )

    try:
        policy.agent(agent_name)
    except KeyError:
        return finish(
            Effect.DENY,
            "agent.unknown",
            f"Denied by default: unknown agent {agent_name}.",
        )
    try:
        effective = effective_for_agent(policy, agent_name)
    except PolicyError as exc:
        return finish(Effect.DENY, "policy.invalid", f"Denied: {exc}.")
    checks.append(Check("pass", "agent.known", f"{agent_name} is bound to role {effective.role}."))

    tool = manifest.tool(tool_name)
    if tool is None:
        return finish(
            Effect.DENY,
            "tool.undeclared",
            f"Denied by default: tool {tool_name} is not declared in the tool manifest, so the call was not forwarded.",
            role=effective.role,
        )
    if tool_name not in effective.tools:
        return finish(
            Effect.DENY,
            "tool.not_granted",
            f"Denied by default: tool {tool_name} is not granted to {agent_name} (role {effective.role}).",
            role=effective.role,
        )
    checks.append(Check("pass", "tool.granted", f"{tool_name} is declared and granted."))

    classified, problem = _classify(tool, args)
    if problem is not None:
        rule, reason = problem
        return finish(Effect.DENY, rule, reason, role=effective.role)

    for item in classified:
        found: tuple[str, str] | None = None
        if item.kind == "path":
            found = path_problem(item, effective)
        elif item.kind == "url":
            found = url_problem(item, effective)
        elif item.kind == "table":
            found = table_problem(item, effective)
        elif item.kind == "recipient":
            found = recipient_problem(item, effective)
        if found is not None:
            rule, reason = found
            return finish(Effect.DENY, rule, reason, role=effective.role)
    checks.append(Check("pass", "scope.accepted", "Paths, URLs, tables, and recipients are inside scope."))

    amount, spend_problem = _sum_spend(classified)
    if spend_problem is not None:
        rule, reason = spend_problem
        return finish(Effect.DENY, rule, reason, role=effective.role, spend=amount)
    if amount is not None:
        ceiling = _spend_ceiling_problem(effective, amount, history, moment, spent=spent)
        if ceiling is not None:
            rule, reason = ceiling
            return finish(Effect.DENY, rule, reason, role=effective.role, spend=amount)
        checks.append(Check("pass", "spend.within_ceiling", "The amount is inside the spend ceilings."))

    rate_problem = _rate_problem(effective, tool_name, history, moment)
    if rate_problem is not None:
        rule, reason = rate_problem
        return finish(Effect.DENY, rule, reason, role=effective.role, spend=amount)

    denial = _matching_denial(approvals, agent_name, token, moment)
    if denial is not None:
        return finish(
            Effect.DENY,
            "approval.denied",
            f"Denied: {denial.get('by', 'an operator')} denied this exact call.",
            role=effective.role,
            spend=amount,
        )

    hold = _matching_approval(effective, tool_name, args)
    if hold is not None:
        approval = _matching_grant(approvals, agent_name, token, moment)
        if approval is None:
            return finish(
                Effect.REQUIRE_APPROVAL,
                "approval.required",
                f"Needs a person: {format_usd(amount) + ' on ' if amount else ''}{tool_name}. "
                f"{hold.reason} ({hold.label()}).",
                role=effective.role,
                spend=amount,
            )
        today, total = _totals(history, agent_name, moment, spent)
        return finish(
            Effect.ALLOW,
            "approval.matched",
            _allow_reason(
                effective,
                tool_name,
                classified,
                amount,
                today,
                total,
                approval_note=(f"Approval {approval['id']} by {approval.get('by', 'someone')} covers this call."),
            ),
            role=effective.role,
            spend=amount,
            approval_id=str(approval["id"]),
        )

    today, total = _totals(history, agent_name, moment, spent)
    return finish(
        Effect.ALLOW,
        "allow",
        _allow_reason(effective, tool_name, classified, amount, today, total, approval_note=None),
        role=effective.role,
        spend=amount,
    )


def _classify(
    tool: ToolSpec,
    arguments: Mapping[str, Any],
) -> tuple[list[Classified], tuple[str, str] | None]:
    declared = {arg.name: arg for arg in tool.arguments}
    for arg in tool.arguments:
        if arg.required and arg.name not in arguments:
            return [], (
                "args.missing",
                f"Denied: {tool.name} is missing required argument {arg.name}.",
            )
    items: list[Classified] = []
    for name, value in arguments.items():
        spec = declared.get(name)
        reserved = RESERVED_ARGUMENT_KINDS.get(name)
        if spec is None and reserved is None:
            continue
        kind = spec.type if spec is not None else reserved
        if reserved in _SENSITIVE:
            if spec is None:
                kind = reserved
            elif spec.type != reserved and spec.type not in _SENSITIVE:
                # A manifest cannot relabel path/url/recipient/spend as a plain string.
                kind = reserved
            elif spec.type in _SENSITIVE and spec.type != reserved:
                return [], (
                    "args.conflict",
                    f"Denied: argument {name} is classified as {spec.type} and as {reserved}.",
                )
        assert kind is not None
        items.append(Classified(name=name, kind=kind, value=value))
    return items, None


def _sum_spend(
    items: Sequence[Classified],
) -> tuple[int | None, tuple[str, str] | None]:
    total = 0
    seen = False
    for item in items:
        if item.kind != "spend":
            continue
        seen = True
        cents, problem = spend_cents(item)
        if problem is not None:
            return None, problem
        assert cents is not None
        total += cents
    if not seen:
        return None, None
    return total, None


def _totals(
    history: Sequence[Mapping[str, Any]],
    agent: str,
    now: datetime,
    spent: tuple[int, int] | None,
) -> tuple[int, int]:
    if spent is not None:
        return spent
    return committed_spend(history, agent=agent, now=now)


def _spend_ceiling_problem(
    effective: Effective,
    amount: int,
    history: Sequence[Mapping[str, Any]],
    now: datetime,
    *,
    spent: tuple[int, int] | None = None,
) -> tuple[str, str] | None:
    spend = effective.spend
    if spend.per_call is None:
        return (
            "spend.no_ceiling",
            f"Denied: {effective.subject} can move money but role {effective.role} "
            "sets no per-call ceiling (deny by default).",
        )
    if amount > spend.per_call:
        return (
            "spend.per_call",
            f"Denied: {format_usd(amount)} exceeds the per-call ceiling of {format_usd(spend.per_call)}.",
        )
    today, total = _totals(history, effective.subject, now, spent)
    if spend.per_day is None:
        return (
            "spend.no_ceiling",
            f"Denied: role {effective.role} sets no per-day ceiling (deny by default).",
        )
    if today + amount > spend.per_day:
        return (
            "spend.per_day",
            f"Denied: {format_usd(amount)} would bring today's spend to "
            f"{format_usd(today + amount)}, over the daily ceiling of {format_usd(spend.per_day)}.",
        )
    if spend.total is None:
        return (
            "spend.no_ceiling",
            f"Denied: role {effective.role} sets no total ceiling (deny by default).",
        )
    if total + amount > spend.total:
        return (
            "spend.total",
            f"Denied: {format_usd(amount)} would bring the running total to "
            f"{format_usd(total + amount)}, over the total ceiling of {format_usd(spend.total)}.",
        )
    return None


def _rate_problem(
    effective: Effective,
    tool_name: str,
    history: Sequence[Mapping[str, Any]],
    now: datetime,
) -> tuple[str, str] | None:
    window_start = now - timedelta(seconds=60)
    for limit in effective.rates:
        if tool_name not in limit.tools:
            continue
        count = 0
        for event in history:
            if event.get("agent") != effective.subject or event.get("tool") not in limit.tools:
                continue
            if event.get("effect") not in {Effect.ALLOW.value, Effect.REQUIRE_APPROVAL.value}:
                continue
            moment = _event_time(event.get("ts"))
            if moment is None or moment < window_start or moment > now:
                continue
            count += 1
        if count >= limit.per_minute:
            return (
                "rate.per_minute",
                f"Denied: {tool_name} is over the {limit.name} limit of {limit.per_minute} calls per minute.",
            )
    return None


def _event_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _matching_approval(
    effective: Effective,
    tool_name: str,
    arguments: Mapping[str, Any],
) -> ApprovalRule | None:
    for rule in effective.approvals:
        if tool_name not in rule.tools:
            continue
        if rule.always:
            return rule
        assert rule.arg is not None and rule.gt_cents is not None
        if rule.arg not in arguments:
            continue
        try:
            value = parse_cents(arguments[rule.arg], field=rule.arg)
        except PolicyError:
            continue
        if value > rule.gt_cents:
            return rule
    return None


def _matching_denial(
    approvals: Sequence[Mapping[str, Any]],
    agent: str,
    token: str,
    now: datetime,
) -> Mapping[str, Any] | None:
    chosen: Mapping[str, Any] | None = None
    for record in approvals:
        if record.get("type") != "denial":
            continue
        if record.get("agent") != agent or record.get("fingerprint") != token:
            continue
        expiry = record.get("expires")
        if not isinstance(expiry, str):
            continue
        try:
            expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except ValueError:
            continue
        if expires_at <= now:
            continue
        chosen = record
    return chosen


def _matching_grant(
    approvals: Sequence[Mapping[str, Any]],
    agent: str,
    token: str,
    now: datetime,
) -> Mapping[str, Any] | None:
    consumed: set[str] = set()
    for record in approvals:
        if record.get("type") == "consumed" and record.get("approval_id"):
            consumed.add(str(record["approval_id"]))
    chosen: Mapping[str, Any] | None = None
    for record in approvals:
        if record.get("type") not in (None, "approval"):
            continue
        if record.get("agent") != agent or record.get("fingerprint") != token:
            continue
        approval_id = str(record.get("id", ""))
        if not approval_id or approval_id in consumed:
            continue
        expiry = record.get("expires")
        if not isinstance(expiry, str):
            continue
        try:
            expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except ValueError:
            continue
        if expires_at <= now:
            continue
        chosen = record
    return chosen


def _allow_reason(
    effective: Effective,
    tool_name: str,
    items: Sequence[Classified],
    amount: int | None,
    today: int,
    total: int,
    *,
    approval_note: str | None,
) -> str:
    parts = [f"Allowed: {tool_name} is granted to {effective.subject}."]
    for item in items:
        if item.kind == "path" and isinstance(item.value, str):
            parts.append(f"Path {item.value} is in scope.")
        elif item.kind == "url" and isinstance(item.value, str):
            parts.append(f"URL {item.value} is in scope.")
        elif item.kind == "table" and isinstance(item.value, str):
            parts.append(f"Table {item.value} is in scope.")
        elif item.kind == "recipient" and isinstance(item.value, str):
            parts.append(f"Recipient {item.value} is allowed.")
    if amount is not None and effective.spend.per_call is not None:
        parts.append(
            f"{format_usd(amount)} is within the per-call ceiling of "
            f"{format_usd(effective.spend.per_call)}. "
            f"Today {format_usd(today + amount)} of {format_usd(effective.spend.per_day)}. "
            f"Total {format_usd(total + amount)} of {format_usd(effective.spend.total)}."
        )
    if approval_note:
        parts.append(approval_note)
    return " ".join(parts)
