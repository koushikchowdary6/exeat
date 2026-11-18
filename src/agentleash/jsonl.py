# SPDX-License-Identifier: MIT
"""Append-only JSONL. Records are added; this module never truncates or rewrites."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any

from agentleash.canon import dumps_line
from agentleash.errors import AgentleashError


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = dumps_line(record)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            text = handle.read()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AgentleashError(f"{path}:{index} is not valid JSON") from exc
        if not isinstance(value, dict):
            raise AgentleashError(f"{path}:{index} must be a JSON object")
        rows.append(value)
    return rows
