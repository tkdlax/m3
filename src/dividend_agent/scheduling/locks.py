"""Single active execution worker lock (spec §13 / §15) — atomic acquire/renew."""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import timedelta
from uuid import uuid4

from dividend_agent.scheduling.calendar import now_utc


class LockBusyError(RuntimeError):
    """Another worker holds the scheduler lock."""


class SchedulerLock:
    """SQLite-backed lease lock with atomic acquire and renewal."""

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
        """Atomically acquire or steal an expired lease.

        Uses BEGIN IMMEDIATE plus a conditional write so two workers cannot both
        observe an empty/expired row and both succeed.
        """
        now = now_utc().replace(microsecond=0)
        expires = now + timedelta(seconds=self.ttl_seconds)
        now_s = now.isoformat()
        exp_s = expires.isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT holder, expires_at FROM scheduler_locks WHERE lock_name = ?",
                (self.lock_name,),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO scheduler_locks (lock_name, holder, acquired_at, expires_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (self.lock_name, self.holder, now_s, exp_s),
                )
                self._conn.execute("COMMIT")
                return
            holder = row["holder"]
            exp = str(row["expires_at"])
            if holder != self.holder and exp > now_s:
                self._conn.execute("ROLLBACK")
                raise LockBusyError(f"scheduler lock held by {holder} until {exp}")
            # Same holder renew, or expired lease steal — single UPDATE.
            cur = self._conn.execute(
                """
                UPDATE scheduler_locks
                SET holder = ?, acquired_at = ?, expires_at = ?
                WHERE lock_name = ?
                  AND (holder = ? OR expires_at <= ?)
                """,
                (self.holder, now_s, exp_s, self.lock_name, self.holder, now_s),
            )
            if cur.rowcount != 1:
                self._conn.execute("ROLLBACK")
                raise LockBusyError(f"scheduler lock race lost for {self.lock_name}")
            self._conn.execute("COMMIT")
        except LockBusyError:
            raise
        except Exception:
            with contextlib.suppress(Exception):
                self._conn.execute("ROLLBACK")
            raise

    def renew(self) -> None:
        """Extend lease TTL; fail closed if we no longer hold the lock."""
        now = now_utc().replace(microsecond=0)
        expires = now + timedelta(seconds=self.ttl_seconds)
        cur = self._conn.execute(
            """
            UPDATE scheduler_locks
            SET expires_at = ?
            WHERE lock_name = ? AND holder = ? AND expires_at > ?
            """,
            (
                expires.isoformat(),
                self.lock_name,
                self.holder,
                now.isoformat(),
            ),
        )
        if cur.rowcount != 1:
            raise LockBusyError(
                f"scheduler lock renew failed for {self.holder} on {self.lock_name}"
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
