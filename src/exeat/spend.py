# SPDX-License-Identifier: MIT
"""Spend already committed in the audit log.

An allow is reserved before the tool runs. A later ``release`` line undoes it
if the tool fails. The file is append-only; nothing here rewrites history.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any


def committed_spend(
    events: Sequence[Mapping[str, Any]],
    *,
    agent: str,
    now: datetime,
) -> tuple[int, int]:
    """Return ``(today_cents, total_cents)`` still reserved for ``agent``."""

    live: dict[str, tuple[int, datetime]] = {}
    for event in events:
        if event.get("agent") != agent:
            continue
        effect = event.get("effect")
        if effect == "allow" and event.get("committed") is True and event.get("id"):
            cents = event.get("spend_cents") or 0
            if isinstance(cents, bool) or not isinstance(cents, int):
                continue
            stamp = _parse_time(event.get("ts"))
            if stamp is None:
                continue
            live[str(event["id"])] = (cents, stamp)
        elif effect == "release" and event.get("reverses"):
            live.pop(str(event["reverses"]), None)
    day = now.astimezone(UTC).date()
    total = 0
    today = 0
    for cents, stamp in live.values():
        total += cents
        if stamp.astimezone(UTC).date() == day:
            today += cents
    return today, total


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
