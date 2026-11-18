# SPDX-License-Identifier: MIT
"""Print why a call was allowed, denied, or held."""

from __future__ import annotations

from agentleash.decision import Decision


def render_explanation(decision: Decision) -> str:
    lines = [
        f"{decision.effect.value}  {decision.tool}  {decision.agent}",
        f"rule  {decision.rule}",
        decision.reason,
        "",
        "checks",
    ]
    if not decision.checks:
        lines.append("  (no trace)")
    for check in decision.checks:
        lines.append(f"  {check.status:<4}  {check.rule:<22}  {check.detail}")
    return "\n".join(lines) + "\n"
