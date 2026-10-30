"""Single active execution worker lock (spec §13 / §15)."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from uuid import uuid4

from dividend_agent.scheduling.calendar import now_utc


class LockBusyError(RuntimeError):
    """Another worker holds the scheduler lock."""


class SchedulerLock:
    """SQLite-backed lease lock. Prevents duplicate concurrent schedulers."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        lock_name: str = "scheduler_worker",
        ttl_seconds: int = 300,
        holder: str | None = None,
    ) -> None:
        self._conn = conn
        self.lock_name = lock_name
        self.ttl_seconds = ttl_seconds
        self.holder = holder or f"worker-{uuid4().hex[:12]}"

    def acquire(self) -> None:
        now = now_utc()
        expires = now + timedelta(seconds=self.ttl_seconds)
        row = self._conn.execute(
            "SELECT holder, expires_at FROM scheduler_locks WHERE lock_name = ?",
            (self.lock_name,),
        ).fetchone()
        if row is not None:
            exp = row["expires_at"]
            holder = row["holder"]
            # Expired lease can be stolen
            if holder != self.holder and str(exp) > now.replace(microsecond=0).isoformat():
                raise LockBusyError(f"scheduler lock held by {holder} until {exp}")
        self._conn.execute(
            """
            INSERT INTO scheduler_locks (lock_name, holder, acquired_at, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(lock_name) DO UPDATE SET
                holder=excluded.holder,
                acquired_at=excluded.acquired_at,
                expires_at=excluded.expires_at
            """,
            (
                self.lock_name,
                self.holder,
                now.replace(microsecond=0).isoformat(),
                expires.replace(microsecond=0).isoformat(),
            ),
        )

    def release(self) -> None:
        self._conn.execute(
            "DELETE FROM scheduler_locks WHERE lock_name = ? AND holder = ?",
            (self.lock_name, self.holder),
        )

    def __enter__(self) -> SchedulerLock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
