# SPDX-License-Identifier: MIT
"""Clocks so tests can pin the UTC day boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


def isoformat(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware UTC time."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._moment = moment.astimezone(UTC)

    def now(self) -> datetime:
        return self._moment
