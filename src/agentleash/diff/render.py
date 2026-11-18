# SPDX-License-Identifier: MIT
"""Text and JSON renderings of a permission diff."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict

from agentleash.brand import PRODUCT_SLUG
from agentleash.diff.compare import Finding, should_fail

_LABEL = {"high": "HIGH", "medium": "MED", "low": "LOW", "note": "NOTE"}
_COLOR = {"high": "31", "medium": "33", "low": "36", "note": "2"}


def counts(findings: list[Finding]) -> dict[str, int]:
    tally = {"high": 0, "medium": 0, "low": 0, "note": 0}
    for finding in findings:
        tally[finding.severity] += 1
    return tally


def render_text(
    findings: list[Finding],
    *,
    base_label: str,
    head_label: str,
    fail_on: str,
    color: bool | None = None,
) -> str:
    use_color = sys.stdout.isatty() if color is None else color
    if os.environ.get("NO_COLOR"):
        use_color = False
    lines = [
        f"{PRODUCT_SLUG} permission diff",
        f"base  {base_label}",
        f"head  {head_label}",
        "",
    ]
    if not findings:
        lines.append("No permission changes.")
    for finding in findings:
        label = _paint(use_color, finding.severity, _LABEL[finding.severity])
        lines.append(f"{label:<4}  {finding.subject}")
        lines.append(f"      {finding.message}")
    summary, _failing = summary_line(findings, fail_on)
    lines.extend(["", summary])
    return "\n".join(lines) + "\n"


def render_json(
    findings: list[Finding],
    *,
    base_label: str,
    head_label: str,
    fail_on: str,
) -> str:
    failing = should_fail(findings, fail_on)
    payload = {
        "base": base_label,
        "head": head_label,
        "fail_on": fail_on,
        "fail": failing,
        "counts": counts(findings),
        "findings": [asdict(item) for item in findings],
    }
    return json.dumps(payload, indent=2) + "\n"


def summary_line(findings: list[Finding], fail_on: str) -> tuple[str, bool]:
    tally = counts(findings)
    failing = should_fail(findings, fail_on)
    text = f"{tally['high']} high, {tally['medium']} medium, {tally['low']} low, {tally['note']} note."
    if failing:
        text += f" Risky escalation: failing CI at {fail_on} and above."
    elif findings:
        text += " No risky escalation."
    else:
        text += " No risky escalation."
    return text, failing


def _paint(enabled: bool, severity: str, text: str) -> str:
    if not enabled:
        return text
    return f"\033[{_COLOR[severity]}m{text}\033[0m"
