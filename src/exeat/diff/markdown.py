# SPDX-License-Identifier: MIT
"""Markdown table for a pull-request comment."""

from __future__ import annotations

from exeat.diff.compare import Finding
from exeat.diff.render import summary_line


def render_markdown(
    findings: list[Finding],
    *,
    base_label: str,
    head_label: str,
    fail_on: str,
) -> str:
    lines = [
        f"Permission diff `{base_label}` → `{head_label}`",
        "",
        "| Severity | Subject | Change |",
        "| --- | --- | --- |",
    ]
    if not findings:
        lines.append("| note | policy | No permission changes. |")
    for finding in findings:
        message = finding.message.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {finding.severity} | {finding.subject} | {message} |")
    summary, _failing = summary_line(findings, fail_on)
    lines.extend(["", summary])
    return "\n".join(lines) + "\n"
