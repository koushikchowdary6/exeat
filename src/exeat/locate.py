# SPDX-License-Identifier: MIT
"""Find the finance example inside a checkout."""

from __future__ import annotations

from pathlib import Path

from exeat.errors import ExeatError


def encord_example_dir() -> Path:
    return _example_dir("encord-merlin-guard")


def finance_example_dir() -> Path:
    return _example_dir("finance-assistant")


def _example_dir(name: str) -> Path:
    marker = Path("examples") / name / "agent-policy.yaml"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / marker
        if candidate.is_file():
            return candidate.parent
    raise ExeatError(f"could not find examples/{name}. Run from a git checkout of this project.")
