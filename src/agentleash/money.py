# SPDX-License-Identifier: Apache-2.0
"""USD amounts as integer cents."""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from agentleash.errors import PolicyError

_CENT = Decimal("0.01")


def parse_cents(value: object, *, field: str) -> int:
    """Parse a non-negative dollar amount into cents.

    Rejects booleans, NaN, infinities, and fractions of a cent. Zero is a
    valid ceiling (no spend allowed) but is not a valid transfer amount;
    callers that require a positive amount check that themselves.
    """

    if isinstance(value, bool) or value is None:
        raise PolicyError(f"{field} must be a USD amount, not {type(value).__name__}")
    try:
        if isinstance(value, Decimal):
            amount = value
        elif isinstance(value, int):
            amount = Decimal(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise PolicyError(f"{field} must be a finite USD amount")
            amount = Decimal(str(value))
        elif isinstance(value, str):
            amount = Decimal(value.strip())
        else:
            raise PolicyError(f"{field} must be a USD amount, not {type(value).__name__}")
    except InvalidOperation as exc:
        raise PolicyError(f"{field} must be a USD amount") from exc
    if not amount.is_finite():
        raise PolicyError(f"{field} must be a finite USD amount")
    if amount < 0:
        raise PolicyError(f"{field} cannot be negative")
    quantized = amount.quantize(_CENT)
    if quantized != amount:
        raise PolicyError(f"{field} cannot use fractions of a cent")
    return int(quantized * 100)


def format_usd(cents: int | None) -> str:
    """Format cents as ``$1,234.50``. ``None`` means no ceiling was set."""

    if cents is None:
        return "unset"
    sign = "-" if cents < 0 else ""
    whole, rem = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{rem:02d}"
