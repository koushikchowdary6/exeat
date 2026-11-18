# SPDX-License-Identifier: MIT
"""SARIF 2.1.0 for a permission diff. High and medium are errors."""

from __future__ import annotations

import json

from agentleash.brand import PRODUCT_SLUG, VERSION
from agentleash.diff.compare import Finding

_LEVEL = {"high": "error", "medium": "error", "low": "warning", "note": "note"}


def render_sarif(findings: list[Finding], *, policy_path: str) -> str:
    rules: list[dict[str, object]] = []
    seen: set[str] = set()
    results: list[dict[str, object]] = []
    for finding in findings:
        if finding.code not in seen:
            seen.add(finding.code)
            rules.append(
                {
                    "id": finding.code,
                    "shortDescription": {"text": finding.code},
                    "defaultConfiguration": {"level": _LEVEL[finding.severity]},
                }
            )
        results.append(
            {
                "ruleId": finding.code,
                "level": _LEVEL[finding.severity],
                "message": {"text": f"{finding.subject}: {finding.message}"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": policy_path},
                        }
                    }
                ],
            }
        )
    document = {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": PRODUCT_SLUG,
                        "version": VERSION,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(document, indent=2) + "\n"
