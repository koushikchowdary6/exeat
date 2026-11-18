# SPDX-License-Identifier: Apache-2.0
"""The runtime gate between an agent and a tool."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from agentleash.decision import ApprovalRequired, Decision, Denied, Effect
from agentleash.errors import PolicyError
from agentleash.policy.effective import effective_for_agent
from agentleash.policy.evaluate import evaluate
from agentleash.policy.load import load_manifest, load_policy
from agentleash.policy.model import Effective, Manifest, Policy
from agentleash.runtime.approvals import ApprovalLog
from agentleash.runtime.audit import AuditLog
from agentleash.runtime.clock import Clock, SystemClock

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
    ) -> None:
        self.policy = policy
        self.manifest = manifest
        self.agent = agent
        self.audit = audit
        self.approvals = approvals
        self.clock = clock or SystemClock()
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
    ) -> Leash:
        chosen = clock or SystemClock()
        return cls(
            load_policy(policy_path),
            load_manifest(manifest_path),
            agent,
            AuditLog(audit_path, chosen),
            ApprovalLog(approval_path, chosen),
            chosen,
        )

    def decide(self, tool: str, arguments: dict[str, Any]) -> Decision:
        return evaluate(
            self.policy,
            self.manifest,
            self.agent,
            tool,
            arguments,
            history=self.audit.read(),
            approvals=self.approvals.read(),
            now=self.clock.now(),
            policy_hash=self.policy.content_hash(),
        )

    def run(
        self,
        tool: str,
        arguments: dict[str, Any],
        execute: Callable[[], T] | None,
    ) -> tuple[Decision, T | None]:
        decision = self.decide(tool, arguments)
        will_run = decision.effect is Effect.ALLOW and execute is not None
        audit_id = self.audit.record(decision, commit=will_run)
        if will_run and decision.approval_id is not None:
            self.approvals.consume(decision.approval_id, audit_id=audit_id)
        if not will_run or execute is None:
            return decision, None
        try:
            result = execute()
        except Exception as exc:
            self.audit.release(audit_id, agent=self.agent, tool=tool, reason=str(exc))
            raise
        return decision, result

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
