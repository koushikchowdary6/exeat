# SPDX-License-Identifier: MIT
"""Run the finance scenario through the same gate the decorator and MCP use."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from exeat.decision import Decision, Effect
from exeat.demo.backend import FinanceBackend
from exeat.demo.scenario import Step, finance_steps
from exeat.locate import finance_example_dir
from exeat.policy.load import load_manifest, load_policy
from exeat.runtime.approvals import ApprovalLog
from exeat.runtime.audit import AuditLog
from exeat.runtime.clock import Clock, SystemClock
from exeat.runtime.leash import Leash


@dataclass(frozen=True)
class StepResult:
    step: Step
    decision: Decision
    output: dict[str, Any] | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.decision.effect is self.step.expect and self.decision.rule == self.step.expect_rule


def run_demo(
    *,
    root: Path | None = None,
    audit_path: Path | None = None,
    approval_path: Path | None = None,
    clock: Clock | None = None,
    by: str = "demo-controller",
) -> list[StepResult]:
    example = root or finance_example_dir()
    chosen = clock or SystemClock()
    policy = load_policy(example / "agent-policy.yaml")
    manifest = load_manifest(example / "tool-manifest.yaml")
    audit = AuditLog(audit_path or example / ".exeat" / "audit.jsonl", chosen)
    approvals = ApprovalLog(approval_path or example / ".exeat" / "approvals.jsonl", chosen)
    backend = FinanceBackend(example)
    leashes = {agent.name: Leash(policy, manifest, agent.name, audit, approvals, chosen) for agent in policy.agents}
    results: list[StepResult] = []
    for step in finance_steps():
        leash = leashes[step.agent]
        if step.approve_before:
            approvals.grant(
                agent=step.agent,
                tool=step.tool,
                arguments=dict(step.arguments),
                by=by,
            )
        decision, output = leash.run(
            step.tool,
            dict(step.arguments),
            _call(backend, step.tool, dict(step.arguments)),
        )
        results.append(StepResult(step=step, decision=decision, output=output, error=None))
    return results


def _call(backend: FinanceBackend, tool: str, arguments: dict[str, Any]) -> Any:
    def execute() -> dict[str, Any]:
        return backend.call(tool, arguments)

    return execute


def render_demo(results: list[StepResult]) -> str:
    lines = [
        "exeat demo  ·  finance-assistant  ·  Northwind Books",
        "deny by default  ·  builtin tools  ·  no network",
        "",
    ]
    allows = denies = holds = 0
    for index, result in enumerate(results, start=1):
        effect = result.decision.effect
        if effect is Effect.ALLOW:
            allows += 1
            word = "ALLOW"
        elif effect is Effect.DENY:
            denies += 1
            word = "DENY"
        else:
            holds += 1
            word = "HOLD"
        lines.append(f"{index:2}  {word:<5}  {result.step.agent:<20}  {result.step.tool}")
        detail = _detail(result)
        if detail:
            lines.append(f"         {detail}")
        lines.append(f"         {result.decision.reason}")
        if result.error:
            lines.append(f"         tool error: {result.error}")
    lines.append("")
    lines.append(f"{len(results)} calls  ·  {allows} allowed  ·  {holds} held  ·  {denies} denied")
    return "\n".join(lines) + "\n"


def _detail(result: StepResult) -> str:
    args = result.step.arguments
    if "amount_usd" in args:
        return f"${args['amount_usd']} to {args.get('vendor_id', '')}"
    if "to" in args:
        extra = f"  bcc {args['bcc']}" if "bcc" in args else ""
        return f"to {args['to']}{extra}"
    if "path" in args:
        return str(args["path"])
    if "table" in args:
        return str(args["table"])
    if "url" in args:
        return str(args["url"])
    if "command" in args:
        return str(args["command"])
    return ""
