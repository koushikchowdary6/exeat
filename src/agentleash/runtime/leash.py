# SPDX-License-Identifier: MIT
"""The runtime gate between an agent and a tool."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from agentleash.brand import LEDGER_FILENAME
from agentleash.decision import ApprovalRequired, Decision, Denied, Effect
from agentleash.errors import PolicyError
from agentleash.ledger import Ledger
from agentleash.policy.effective import effective_for_agent
from agentleash.policy.evaluate import evaluate
from agentleash.policy.load import load_manifest, load_policy
from agentleash.policy.model import Effective, Manifest, Policy
from agentleash.runtime.approvals import ApprovalLog
from agentleash.runtime.audit import AuditLog
from agentleash.runtime.clock import Clock, SystemClock
from agentleash.webhook import notify_hold

T = TypeVar("T")


class Leash:
    """Decides and records one agent's tool calls.

    ``decide`` is pure. ``run`` writes the audit log. An allow is reserved
    before the tool body runs and released if the body raises, so a crash
    does not grant a free spend. Approvals do not raise ceilings.
    """

    def __init__(
        self,
        policy: Policy,
        manifest: Manifest,
        agent: str,
        audit: AuditLog,
        approvals: ApprovalLog,
        clock: Clock | None = None,
        ledger: Ledger | None = None,
        webhook_url: str | None = None,
    ) -> None:
        self.policy = policy
        self.manifest = manifest
        self.agent = agent
        self.audit = audit
        self.approvals = approvals
        self.clock = clock or SystemClock()
        self.ledger = ledger
        self.webhook_url = webhook_url
        self.effective: Effective = effective_for_agent(policy, agent)
        self._check_manifest()

    @classmethod
    def open(
        cls,
        *,
        policy_path: Path,
        manifest_path: Path,
        agent: str,
        audit_path: Path,
        approval_path: Path,
        clock: Clock | None = None,
        ledger_path: Path | None = None,
        webhook_url: str | None = None,
    ) -> Leash:
        chosen = clock or SystemClock()
        ledger = Ledger.sqlite(ledger_path or audit_path.parent / LEDGER_FILENAME)
        return cls(
            load_policy(policy_path),
            load_manifest(manifest_path),
            agent,
            AuditLog(audit_path, chosen),
            ApprovalLog(approval_path, chosen),
            chosen,
            ledger,
            webhook_url,
        )

    def decide(self, tool: str, arguments: dict[str, Any]) -> Decision:
        spent = None
        if self.ledger is not None:
            spent = self.ledger.totals(self.agent, self.clock.now())
        return evaluate(
            self.policy,
            self.manifest,
            self.agent,
            tool,
            arguments,
            history=() if self.ledger is not None else self.audit.read(),
            approvals=self.approvals.read(),
            now=self.clock.now(),
            policy_hash=self.policy.content_hash(),
            spent=spent,
        )

    def run(
        self,
        tool: str,
        arguments: dict[str, Any],
        execute: Callable[[], T] | None,
    ) -> tuple[Decision, T | None]:
        decision = self.decide(tool, arguments)
        if decision.effect is Effect.REQUIRE_APPROVAL:
            notify_hold(self.webhook_url, decision)
        if decision.effect is not Effect.ALLOW or execute is None:
            self.audit.record(decision, commit=False)
            return decision, None
        reservation_id = self._reserve(decision)
        if reservation_id is False:
            decision = self.decide(tool, arguments)
            self.audit.record(decision, commit=False)
            return decision, None
        audit_id = self.audit.record(decision, commit=True)
        if decision.approval_id is not None:
            self.approvals.consume(decision.approval_id, audit_id=audit_id)
        try:
            result = execute()
        except Exception as exc:
            if isinstance(reservation_id, str) and self.ledger is not None:
                self.ledger.release(reservation_id)
            self.audit.release(audit_id, agent=self.agent, tool=tool, reason=str(exc))
            raise
        if isinstance(reservation_id, str) and self.ledger is not None:
            self.ledger.commit(reservation_id)
        return decision, result

    def _reserve(self, decision: Decision) -> str | bool | None:
        """Reserve spend. ``False`` means the atomic check denied the call."""

        if self.ledger is None or not decision.spend_cents:
            return None
        ceilings = self.effective.spend
        reserved = self.ledger.try_reserve(
            agent=self.agent,
            tool=decision.tool,
            amount_cents=decision.spend_cents,
            per_call=ceilings.per_call,
            per_day=ceilings.per_day,
            total=ceilings.total,
            now=self.clock.now(),
        )
        if reserved.ok and reserved.reservation_id is not None:
            return reserved.reservation_id
        return False

    def guard(self, tool_name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """Decorator for a plain function. Denied and held calls raise."""

        def decorate(fn: Callable[..., T]) -> Callable[..., T]:
            signature = inspect.signature(fn)

            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> T:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                decision, result = self.run(
                    tool_name,
                    dict(bound.arguments),
                    lambda: fn(*args, **kwargs),
                )
                if decision.effect is Effect.DENY:
                    raise Denied(decision)
                if decision.effect is Effect.REQUIRE_APPROVAL:
                    raise ApprovalRequired(decision)
                return result  # type: ignore[return-value]

            return wrapper

        return decorate

    def _check_manifest(self) -> None:
        for tool_name in sorted(self.effective.tools):
            if self.manifest.tool(tool_name) is None:
                raise PolicyError(f"{self.agent} is granted {tool_name}, which is not in the tool manifest")
        for rule in self.effective.approvals:
            for tool_name in sorted(rule.tools):
                spec = self.manifest.tool(tool_name)
                if spec is None:
                    raise PolicyError(f"approval {rule.name} references {tool_name}, which is not in the tool manifest")
                if rule.always or rule.arg is None:
                    continue
                argument = spec.argument(rule.arg)
                if argument is None or argument.type not in {"spend", "number"}:
                    raise PolicyError(
                        f"approval {rule.name} watches {tool_name}.{rule.arg}, which is not a numeric manifest argument"
                    )
