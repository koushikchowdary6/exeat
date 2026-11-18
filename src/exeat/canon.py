# SPDX-License-Identifier: MIT
"""Stable JSON shapes for fingerprints and the audit log."""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any

_CENT = Decimal("0.01")


def canon(value: Any) -> Any:
    """Normalize values so ``900``, ``900.0``, and ``"900.00"`` agree.

    Numeric values become fixed two-decimal strings. Booleans stay booleans
    (they are not treated as ``0`` and ``1``). Mapping keys are sorted.
    """

    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
        return f"{amount.quantize(_CENT):.2f}"
    if isinstance(value, str):
        normalized = _plain_number(value.strip())
        if normalized is not None:
            return normalized
        return value
    if isinstance(value, dict):
        return {str(key): canon(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [canon(item) for item in value]
    return str(value)


def _plain_number(text: str) -> str | None:
    """Return a two-decimal form for strings like ``900`` or ``12.50`` only."""

    body = text[1:] if text.startswith("-") else text
    if not body or body == "." or body.count(".") > 1:
        return None
    if not body.replace(".", "", 1).isdigit():
        return None
    amount = Decimal(text)
    if amount != amount.quantize(_CENT):
        return text
    return f"{amount.quantize(_CENT):.2f}"


def fingerprint(agent: str, tool: str, arguments: Any) -> str:
    """Short id bound to the agent, the tool, and the full argument set."""

    payload = json.dumps(
        {"agent": agent, "arguments": canon(arguments), "tool": tool},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def truncate(value: Any, limit: int = 500) -> Any:
    """Copy ``value`` with long strings cut, for the audit line."""

    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + "...[truncated]"
    if isinstance(value, dict):
        return {str(key): truncate(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [truncate(item, limit) for item in value]
    return value


def dumps_line(record: dict[str, Any]) -> str:
    """One JSON object, one line, no trailing spaces."""

    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str) + "\n"
