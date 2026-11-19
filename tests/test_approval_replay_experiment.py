"""Turn two documented approval-replay experiments into CI regressions.

An approval must not authorize a modified tool call or an expired call.
These checks use the real example policy and runtime, not a mocked evaluator.
The concurrent replay experiment remains observational until its atomicity
semantics are explicitly established.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def replay_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "approval_replay.py"
    spec = importlib.util.spec_from_file_location("exeat_approval_replay_experiment", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("case", ["different-arguments", "expired"])
def test_approval_does_not_authorize_changed_or_expired_call(replay_module, case: str) -> None:
    lines = replay_module._run(case)
    decisions = [line for line in lines if line.startswith("effect=")]
    assert len(decisions) == 1, lines
    assert "effect=require_approval" in decisions[0], lines
    assert "consumed_records_for_approval: 0" in lines, lines
