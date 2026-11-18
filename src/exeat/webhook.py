# SPDX-License-Identifier: MIT
"""Optional webhook for holds. Unset URL means no call, so the demo stays offline."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from exeat.decision import Decision


def hold_payload(decision: Decision) -> dict[str, Any]:
    return {
        "effect": decision.effect.value,
        "rule": decision.rule,
        "reason": decision.reason,
        "agent": decision.agent,
        "role": decision.role,
        "tool": decision.tool,
        "fingerprint": decision.fingerprint,
        "arguments": decision.arguments,
        "spend_cents": decision.spend_cents,
    }


def notify_hold(url: str | None, decision: Decision, *, timeout: float = 2.0) -> bool:
    """POST the hold. Returns False when there is no URL or the endpoint fails."""

    if not url:
        return False
    body = json.dumps(hold_payload(decision)).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= getattr(response, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
