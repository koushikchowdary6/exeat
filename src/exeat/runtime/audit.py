# SPDX-License-Identifier: MIT
"""The decision tape. Append-only, hash-chained, and never rewritten.

Each line stores ``prev``, the hash of the previous line, and ``hash``,
SHA-256 over the line's own fields except ``hash``. ``verify_chain``
recomputes both. A changed, dropped, or reordered line fails verification.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from exeat.brand import PRODUCT_SLUG, VERSION
from exeat.canon import dumps_line, truncate
from exeat.decision import Decision, Effect
from exeat.errors import ExeatError
from exeat.jsonl import read_jsonl
from exeat.runtime.clock import Clock, SystemClock, isoformat

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: Path, clock: Clock | None = None) -> None:
        self.path = path
        self.clock = clock or SystemClock()

    def read(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path)

    def record(self, decision: Decision, *, commit: bool) -> str:
        audit_id = _new_id("dec")
        self._append(
            {
                "v": 1,
                "id": audit_id,
                "ts": isoformat(self.clock.now()),
                "effect": decision.effect.value,
                "committed": bool(commit and decision.effect is Effect.ALLOW),
                "agent": decision.agent,
                "role": decision.role,
                "tool": decision.tool,
                "rule": decision.rule,
                "reason": decision.reason,
                "arguments": truncate(decision.arguments),
                "fingerprint": decision.fingerprint,
                "policy_hash": decision.policy_hash,
                "spend_cents": decision.spend_cents or 0,
                "approval_id": decision.approval_id,
                "product": PRODUCT_SLUG,
                "product_version": VERSION,
            }
        )
        return audit_id

    def release(self, audit_id: str, *, agent: str, tool: str, reason: str) -> str:
        release_id = _new_id("rel")
        self._append(
            {
                "v": 1,
                "id": release_id,
                "ts": isoformat(self.clock.now()),
                "effect": "release",
                "committed": False,
                "reverses": audit_id,
                "agent": agent,
                "tool": tool,
                "reason": reason,
                "product": PRODUCT_SLUG,
                "product_version": VERSION,
            }
        )
        return release_id

    def _append(self, body: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                prev = _tail_hash(handle.read())
                record = {**body, "prev": prev}
                record["hash"] = chain_hash(record)
                handle.write(dumps_line(record))
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class ChainFailure:
    line: int
    detail: str


def chain_hash(record: dict[str, Any]) -> str:
    """SHA-256 of the record with ``hash`` removed. ``prev`` stays in the body."""

    body = {key: value for key, value in record.items() if key != "hash"}
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def verify_chain(path: Path) -> list[ChainFailure]:
    """Return one failure per broken line. An empty file is a valid empty chain."""

    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    failures: list[ChainFailure] = []
    expected = GENESIS
    seen = False
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        seen = True
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            failures.append(ChainFailure(index, "not valid JSON"))
            expected = ""
            continue
        if not isinstance(value, dict):
            failures.append(ChainFailure(index, "must be a JSON object"))
            expected = ""
            continue
        prev = value.get("prev")
        digest = value.get("hash")
        if not isinstance(prev, str) or not isinstance(digest, str):
            failures.append(ChainFailure(index, "missing prev or hash; this tape is not hash-chained"))
            expected = digest if isinstance(digest, str) else ""
            continue
        if prev != expected:
            failures.append(ChainFailure(index, "prev does not match the previous line"))
        if digest != chain_hash(value):
            failures.append(ChainFailure(index, "hash does not match the line"))
        expected = digest
    if not seen:
        return []
    return failures


def export_audit(path: Path) -> dict[str, Any]:
    """A JSON document of the tape. Verification is reported; it is not required to export."""

    records = read_jsonl(path) if path.exists() else []
    failures = verify_chain(path) if path.exists() else []
    return {
        "version": 1,
        "algorithm": "sha256-chain",
        "product": PRODUCT_SLUG,
        "verified": not failures,
        "failures": [{"line": item.line, "detail": item.detail} for item in failures],
        "records": records,
    }


def _tail_hash(text: str) -> str:
    last = ""
    for line in text.splitlines():
        if line.strip():
            last = line
    if not last:
        return GENESIS
    try:
        value = json.loads(last)
    except json.JSONDecodeError as exc:
        raise ExeatError("audit log tail is not valid JSON; refusing to extend a broken chain") from exc
    if isinstance(value, dict) and isinstance(value.get("hash"), str):
        return str(value["hash"])
    raise ExeatError("audit log tail has no hash; refusing to extend an unchained tape")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"
