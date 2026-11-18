# SPDX-License-Identifier: MIT
"""Spend ledger. SQLite by default, Postgres when given a postgres URL.

A reservation is inserted inside a transaction after the ceilings are read,
so two processes cannot both pass a ceiling that only one of them fits under.
Released rows stop counting. Committed rows keep counting.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentleash.errors import AgentleashError
from agentleash.money import format_usd

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reservations (
    id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    tool TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    state TEXT NOT NULL,
    day TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reservations_agent_state ON reservations(agent, state, day);
CREATE TABLE IF NOT EXISTS agent_lock (
    agent TEXT PRIMARY KEY
);
"""


@dataclass(frozen=True)
class Reservation:
    ok: bool
    reservation_id: str | None
    rule: str | None
    reason: str | None


class Ledger:
    def __init__(self, connection: Any, dialect: str) -> None:
        if dialect not in {"sqlite", "postgres"}:
            raise AgentleashError(f"unknown ledger dialect {dialect}")
        self.connection = connection
        self.dialect = dialect
        self._lock = threading.Lock()
        self._init()

    @classmethod
    def sqlite(cls, path: Path) -> Ledger:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, isolation_level=None, timeout=5, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return cls(connection, "sqlite")

    @classmethod
    def postgres(cls, dsn: str) -> Ledger:
        try:
            import psycopg
        except ImportError as exc:
            raise AgentleashError(
                "Postgres support needs the optional psycopg package. Install the 'postgres' extra."
            ) from exc
        connection = psycopg.connect(dsn, autocommit=True)
        return cls(connection, "postgres")

    @classmethod
    def open(cls, url: str) -> Ledger:
        if url.startswith("sqlite:"):
            raw = url.removeprefix("sqlite://").removeprefix("sqlite:")
            if raw.startswith("//"):
                raw = raw[2:]
            return cls.sqlite(Path(raw or ":memory:"))
        if url.startswith("postgres://") or url.startswith("postgresql://"):
            return cls.postgres(url)
        raise AgentleashError("ledger URL must start with sqlite: or postgres://")

    def totals(self, agent: str, now: datetime) -> tuple[int, int]:
        day = now.date().isoformat()
        with self._lock:
            today = self._sum(agent, day)
            overall = self._sum(agent, None)
        return today, overall

    def try_reserve(
        self,
        *,
        agent: str,
        tool: str,
        amount_cents: int,
        per_call: int | None,
        per_day: int | None,
        total: int | None,
        now: datetime,
    ) -> Reservation:
        day = now.date().isoformat()
        created = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._lock:
            self._begin()
            try:
                self._lock_agent(agent)
                if per_call is None or per_day is None or total is None:
                    self._rollback()
                    return Reservation(False, None, "spend.no_ceiling", "Denied: a spend ceiling is unset.")
                if amount_cents > per_call:
                    self._rollback()
                    return Reservation(
                        False,
                        None,
                        "spend.per_call",
                        f"Denied: {format_usd(amount_cents)} exceeds the per-call ceiling of {format_usd(per_call)}.",
                    )
                today = self._sum(agent, day)
                overall = self._sum(agent, None)
                if today + amount_cents > per_day:
                    self._rollback()
                    return Reservation(
                        False,
                        None,
                        "spend.per_day",
                        f"Denied: {format_usd(amount_cents)} would bring today's spend to "
                        f"{format_usd(today + amount_cents)}, over the daily ceiling of {format_usd(per_day)}.",
                    )
                if overall + amount_cents > total:
                    self._rollback()
                    return Reservation(
                        False,
                        None,
                        "spend.total",
                        f"Denied: {format_usd(amount_cents)} would bring the running total to "
                        f"{format_usd(overall + amount_cents)}, over the total ceiling of {format_usd(total)}.",
                    )
                reservation_id = f"rsv_{uuid4().hex[:12]}"
                self._exec(
                    "INSERT INTO reservations (id, agent, tool, amount_cents, currency, state, day, created_at) "
                    "VALUES (?, ?, ?, ?, 'USD', 'reserved', ?, ?)",
                    (reservation_id, agent, tool, amount_cents, day, created),
                )
                self._commit()
                return Reservation(True, reservation_id, None, None)
            except Exception:
                self._rollback()
                raise

    def commit(self, reservation_id: str) -> None:
        with self._lock:
            self._exec(
                "UPDATE reservations SET state = 'committed' WHERE id = ? AND state = 'reserved'",
                (reservation_id,),
            )

    def release(self, reservation_id: str) -> None:
        with self._lock:
            self._exec(
                "UPDATE reservations SET state = 'released' WHERE id = ? AND state IN ('reserved', 'committed')",
                (reservation_id,),
            )

    def close(self) -> None:
        self.connection.close()

    def _init(self) -> None:
        if self.dialect == "sqlite":
            self.connection.executescript(_SCHEMA)
            return
        for statement in _statements(_SCHEMA):
            self.connection.execute(statement)

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        statement = sql if self.dialect == "sqlite" else sql.replace("?", "%s")
        if self.dialect == "postgres":
            cursor = self.connection.cursor()
            cursor.execute(statement, params)
            return cursor
        return self.connection.execute(statement, params)

    def _begin(self) -> None:
        if self.dialect == "sqlite":
            self.connection.execute("BEGIN IMMEDIATE")
        else:
            self.connection.execute("BEGIN")

    def _commit(self) -> None:
        self.connection.commit() if self.dialect == "postgres" else self.connection.execute("COMMIT")

    def _rollback(self) -> None:
        if self.dialect == "postgres":
            self.connection.rollback()
            return
        try:
            self.connection.execute("ROLLBACK")
        except sqlite3.OperationalError:
            return

    def _lock_agent(self, agent: str) -> None:
        if self.dialect == "sqlite":
            self._exec("INSERT INTO agent_lock (agent) VALUES (?) ON CONFLICT (agent) DO NOTHING", (agent,))
            return
        self._exec("INSERT INTO agent_lock (agent) VALUES (?) ON CONFLICT (agent) DO NOTHING", (agent,))
        self._exec("SELECT agent FROM agent_lock WHERE agent = ? FOR UPDATE", (agent,))

    def _sum(self, agent: str, day: str | None) -> int:
        if day is None:
            cursor = self._exec(
                "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM reservations "
                "WHERE agent = ? AND state IN ('reserved', 'committed')",
                (agent,),
            )
        else:
            cursor = self._exec(
                "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM reservations "
                "WHERE agent = ? AND state IN ('reserved', 'committed') AND day = ?",
                (agent, day),
            )
        row = cursor.fetchone()
        if row is None:
            return 0
        if isinstance(row, sqlite3.Row):
            return int(row["total"])
        if isinstance(row, dict):
            return int(row.get("total", 0))
        return int(row[0])


def _statements(script: str) -> list[str]:
    return [part.strip() for part in script.split(";") if part.strip()]
