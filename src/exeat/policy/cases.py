# SPDX-License-Identifier: MIT
"""Run declared policy cases. A case is one tool call and the effect it must get."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from exeat.errors import ExeatError, PolicyError
from exeat.policy.evaluate import evaluate
from exeat.policy.load import load_manifest, load_policy


@dataclass(frozen=True)
class CaseFailure:
    name: str
    detail: str


def run_cases(path: Path, *, now: datetime | None = None) -> tuple[int, list[CaseFailure]]:
    document = _load(path)
    root = path.parent
    try:
        policy_name = str(document.get("policy") or "agent-policy.yaml")
        manifest_name = str(document.get("manifest") or "tool-manifest.yaml")
        policy = load_policy(root / policy_name)
        manifest = load_manifest(root / manifest_name)
    except PolicyError as exc:
        raise ExeatError(str(exc)) from exc
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ExeatError(f"{path} needs a non-empty cases list")
    moment = now or datetime.now(UTC)
    failures: list[CaseFailure] = []
    for index, raw in enumerate(raw_cases):
        if not isinstance(raw, dict):
            failures.append(CaseFailure(f"cases[{index}]", "case must be a mapping"))
            continue
        name = str(raw.get("name") or f"cases[{index}]")
        agent = raw.get("agent")
        tool = raw.get("tool")
        arguments = raw.get("arguments", raw.get("args", {}))
        expect = raw.get("expect")
        if not isinstance(agent, str) or not isinstance(tool, str) or not isinstance(expect, str):
            failures.append(CaseFailure(name, "agent, tool, and expect are required strings"))
            continue
        if not isinstance(arguments, dict):
            failures.append(CaseFailure(name, "arguments must be a mapping"))
            continue
        decision = evaluate(policy, manifest, agent, tool, arguments, now=moment)
        if decision.effect.value != expect:
            failures.append(CaseFailure(name, f"expected {expect}, got {decision.effect.value}: {decision.reason}"))
            continue
        rule = raw.get("rule")
        if isinstance(rule, str) and decision.rule != rule:
            failures.append(CaseFailure(name, f"expected rule {rule}, got {decision.rule}"))
    return len(raw_cases), failures


def render_case_report(path: Path, count: int, failures: list[CaseFailure]) -> str:
    lines = [f"policy cases  {path}", ""]
    if not failures:
        lines.append(f"{count} passed.")
        return "\n".join(lines) + "\n"
    for failure in failures:
        lines.append(f"FAIL  {failure.name}")
        lines.append(f"      {failure.detail}")
    passed = count - len(failures)
    lines.append("")
    lines.append(f"{passed} passed, {len(failures)} failed.")
    return "\n".join(lines) + "\n"


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ExeatError(f"could not read {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ExeatError(f"could not parse {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ExeatError(f"{path} must be a mapping")
    return loaded
