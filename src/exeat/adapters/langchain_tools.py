# SPDX-License-Identifier: MIT
"""Drop-in wrapper for LangChain and LangGraph tools.

Pass ``tool_factory`` in tests. If it is omitted and ``langchain_core`` is
installed, that package's ``tool`` decorator is used. Otherwise the guarded
function is returned as-is.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar, cast

from exeat.adapters.common import call_guarded
from exeat.runtime.leash import Leash

T = TypeVar("T")


def as_tool(
    leash: Leash,
    tool_name: str,
    fn: Callable[..., T],
    *,
    tool_factory: Callable[[Callable[..., Any]], Any] | None = None,
) -> Any:
    @functools.wraps(fn)
    def guarded(*args: Any, **kwargs: Any) -> T:
        return call_guarded(leash, tool_name, fn, args, kwargs)

    factory = tool_factory if tool_factory is not None else _load_factory()
    if factory is None:
        return guarded
    return factory(guarded)


def _load_factory() -> Callable[[Callable[..., Any]], Any] | None:
    try:
        from langchain_core.tools import tool as langchain_tool
    except ImportError:
        return None
    return cast(Callable[[Callable[..., Any]], Any], langchain_tool)
