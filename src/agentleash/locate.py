# SPDX-License-Identifier: MIT
"""Find the finance example inside a checkout."""

from __future__ import annotations

from pathlib import Path

from agentleash.errors import AgentleashError


def finance_example_dir() -> Path:
    marker = Path("examples") / "finance-assistant" / "agent-policy.yaml"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / marker
        if candidate.is_file():
            return candidate.parent
    raise AgentleashError("could not find examples/finance-assistant. Run from a git checkout of this project.")
