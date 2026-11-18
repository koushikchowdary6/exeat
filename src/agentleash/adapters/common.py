# SPDX-License-Identifier: MIT
"""Shared call wrapper. Framework modules stay optional."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from agentleash.decision import ApprovalRequired, Decision, Denied, Effect
from agentleash.runtime.leash import Leash

T = TypeVar("T")


def call_guarded(
    leash: Leash,
    tool_name: str,
    fn: Callable[..., T],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> T:
    signature = inspect.signature(fn)
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    decision, result = leash.run(tool_name, dict(bound.arguments), lambda: fn(*args, **kwargs))
    if decision.effect is Effect.DENY:
        raise Denied(decision)
    if decision.effect is Effect.REQUIRE_APPROVAL:
        raise ApprovalRequired(decision)
    return result  # type: ignore[return-value]


def decision_of(exc: Denied | ApprovalRequired) -> Decision:
    return exc.decision
