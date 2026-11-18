# SPDX-License-Identifier: MIT
"""Drop-in wrapper for the OpenAI Agents SDK.

The SDK is not imported unless ``sdk_decorator`` is omitted and the package is
installed. Tests pass a fake decorator and never touch the network.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar, cast

from agentleash.adapters.common import call_guarded
from agentleash.runtime.leash import Leash

T = TypeVar("T")


def function_tool(
    leash: Leash,
    tool_name: str,
    *,
    sdk_decorator: Callable[[Callable[..., Any]], Any] | None = None,
) -> Callable[[Callable[..., T]], Any]:
    """Decorate a function so the gate runs before the SDK sees the call."""

    def decorate(fn: Callable[..., T]) -> Any:
        @functools.wraps(fn)
        def guarded(*args: Any, **kwargs: Any) -> T:
            return call_guarded(leash, tool_name, fn, args, kwargs)

        decorator = sdk_decorator if sdk_decorator is not None else _load_sdk_decorator()
        if decorator is None:
            return guarded
        return decorator(guarded)

    return decorate


def _load_sdk_decorator() -> Callable[[Callable[..., Any]], Any] | None:
    try:
        from agents import function_tool as sdk_function_tool
    except ImportError:
        return None
    return cast(Callable[[Callable[..., Any]], Any], sdk_function_tool)
