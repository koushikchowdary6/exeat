# SPDX-License-Identifier: MIT
"""Drop-in wrapper for a CrewAI tool.

``tool_class`` is the CrewAI ``Tool`` constructor in production. Tests pass a
fake. The gate runs inside ``func`` before the original function.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from agentleash.adapters.common import call_guarded
from agentleash.runtime.leash import Leash


def as_crew_tool(
    leash: Leash,
    tool_name: str,
    fn: Callable[..., Any],
    *,
    description: str = "",
    tool_class: Callable[..., Any] | None = None,
) -> Any:
    def func(*args: Any, **kwargs: Any) -> Any:
        return call_guarded(leash, tool_name, fn, args, kwargs)

    factory = tool_class if tool_class is not None else _load_tool_class()
    if factory is None:
        func.tool_name = tool_name  # type: ignore[attr-defined]
        func.description = description  # type: ignore[attr-defined]
        return func
    return factory(name=tool_name, description=description or tool_name, func=func)


def _load_tool_class() -> Callable[..., Any] | None:
    try:
        from crewai.tools import Tool
    except ImportError:
        try:
            from crewai_tools import Tool
        except ImportError:
            return None
    return cast(Callable[..., Any], Tool)
