# SPDX-License-Identifier: Apache-2.0
"""JSON for the local permission board."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agentleash.brand import PRODUCT_SLUG, PRODUCT_TITLE, VERSION
from agentleash.demo.scenario import Step, finance_steps
from agentleash.money import format_usd
from agentleash.policy.effective import effective_for_agent, effective_for_role
from agentleash.policy.model import Manifest, Policy, ScopeSet, SpendLimits
from agentleash.policy.protected import PROTECTED_FILE_GLOBS
from agentleash.spend import committed_spend


def build_snapshot(
    policy: Policy,
    manifest: Manifest,
    events: list[dict[str, Any]],
    *,
    include_presets: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    names = {agent.name for agent in policy.agents}
    roots = [role.name for role in policy.roles if role.inherits is None]
    return {
        "product": {"title": PRODUCT_TITLE, "slug": PRODUCT_SLUG, "version": VERSION},
        "org": {"name": policy.org.name, "unit": policy.org.unit},
        "policy_hash": policy.content_hash(),
        "manifest_hash": manifest.content_hash(),
        "protected": list(PROTECTED_FILE_GLOBS),
        "tree": [role_node(policy, name, events, moment) for name in roots],
        "presets": [
            _preset(step) for step in finance_steps() if include_presets and step.preset and step.agent in names
        ],
        "agents": [agent_node(policy, agent.name, events, moment)["name"] for agent in policy.agents],
        "agent_details": [agent_node(policy, agent.name, events, moment) for agent in policy.agents],
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "risk": tool.risk,
                "arguments": [{"name": arg.name, "type": arg.type, "required": arg.required} for arg in tool.arguments],
            }
            for tool in manifest.tools
        ],
        "audit": events[-200:],
        "audit_count": len(events),
    }


def role_node(
    policy: Policy,
    name: str,
    events: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    role = policy.role(name)
    effective = effective_for_role(policy, name)
    return {
        "name": role.name,
        "description": role.description,
        "inherits": role.inherits,
        "tools": sorted(effective.tools),
        "spend": _spend(effective.spend),
        "recipients": {
            "domains": sorted(effective.recipient_domains),
            "addresses": sorted(effective.recipient_addresses),
        },
        "scopes": {
            "files": _scope(effective.files),
            "urls": _scope(effective.urls),
            "tables": _scope(effective.tables),
        },
        "approvals": [
            {"name": rule.name, "label": rule.label(), "reason": rule.reason} for rule in effective.approvals
        ],
        "agents": [agent_node(policy, agent.name, events, now) for agent in policy.agents if agent.role == name],
        "children": [role_node(policy, child.name, events, now) for child in policy.roles if child.inherits == name],
    }


def agent_node(
    policy: Policy,
    name: str,
    events: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    effective = effective_for_agent(policy, name)
    today, total = committed_spend(events, agent=name, now=now)
    return {
        "name": name,
        "role": effective.role,
        "description": effective.description,
        "tools": sorted(effective.tools),
        "spend": _spend(effective.spend),
        "usage": {
            "today_cents": today,
            "total_cents": total,
            "today_label": format_usd(today),
            "total_label": format_usd(total),
        },
    }


def _preset(step: Step) -> dict[str, Any]:
    return {
        "id": step.id,
        "label": step.label,
        "blurb": step.blurb,
        "agent": step.agent,
        "tool": step.tool,
        "arguments": step.arguments,
        "expect": step.expect.value,
    }


def _spend(spend: SpendLimits) -> dict[str, Any] | None:
    if not spend.is_set():
        return None
    return {
        "per_call_cents": spend.per_call,
        "per_day_cents": spend.per_day,
        "total_cents": spend.total,
        "per_call_label": format_usd(spend.per_call),
        "per_day_label": format_usd(spend.per_day),
        "total_label": format_usd(spend.total),
        "label": spend.label(),
    }


def _scope(scope: ScopeSet) -> dict[str, list[str]]:
    return {"allow": list(scope.allow), "deny": list(scope.deny)}
