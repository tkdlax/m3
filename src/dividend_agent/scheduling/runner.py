"""Idempotent, restart-safe job runner (spec §15)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from dividend_agent.scheduling.calendar import now_utc
from dividend_agent.storage.repository import LedgerRepository


@dataclass
class JobResult:
    job_name: str
    period_key: str
    status: str  # completed | skipped | skipped_holiday | skipped_paused | failed
    detail: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    reused: bool = False


JobFn = Callable[["SchedulerRunner", str], JobResult]


class SchedulerRunner:
    """Runs named jobs once per period_key unless forced."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        jobs: dict[str, JobFn] | None = None,
    ) -> None:
        self.conn = conn
        self.ledger = LedgerRepository(conn)
        self._jobs = jobs or {}

    def register(self, name: str, fn: JobFn) -> None:
        self._jobs[name] = fn

    def get_key(self, job_name: str, period_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM job_keys WHERE job_name = ? AND period_key = ?",
            (job_name, period_key),
        ).fetchone()
        return dict(row) if row else None

    def run(
        self,
        job_name: str,
        period_key: str,
        *,
        force: bool = False,
    ) -> JobResult:
        fn = self._jobs.get(job_name)
        if fn is None:
            raise KeyError(f"unknown job: {job_name}")

        existing = self.get_key(job_name, period_key)
        if (
            existing
            and not force
            and existing["status"] in {"completed", "skipped", "skipped_holiday", "skipped_paused"}
        ):
            return JobResult(
                job_name=job_name,
                period_key=period_key,
                status=existing["status"],
                detail=f"idempotent reuse: {existing.get('result_json', '')[:200]}",
                payload=json.loads(existing.get("result_json") or "{}"),
                reused=True,
            )

        run_id = self.ledger.start_job(job_name)
        try:
            result = fn(self, period_key)
            self.ledger.finish_job(run_id, status=result.status, detail=result.detail)
            self._upsert_key(job_name, period_key, result, run_id)
            self.ledger.append_audit(
                f"scheduler.{job_name}",
                {
                    "period_key": period_key,
                    "status": result.status,
                    "detail": result.detail,
                    "payload": result.payload,
                    "reused": False,
                },
                correlation_id=str(run_id),
            )
            return result
        except Exception as exc:
            self.ledger.finish_job(run_id, status="failed", detail=str(exc))
            failed = JobResult(
                job_name=job_name,
                period_key=period_key,
                status="failed",
                detail=str(exc),
            )
            self._upsert_key(job_name, period_key, failed, run_id)
            self.ledger.append_audit(
                f"scheduler.{job_name}.failed",
                {"period_key": period_key, "error": str(exc)},
                correlation_id=str(run_id),
            )
            raise

    def _upsert_key(self, job_name: str, period_key: str, result: JobResult, run_id: int) -> None:
        now = now_utc().replace(microsecond=0).isoformat()
        self.conn.execute(
            """
            INSERT INTO job_keys (
                job_name, period_key, status, job_run_id, result_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_name, period_key) DO UPDATE SET
                status=excluded.status,
                job_run_id=excluded.job_run_id,
                result_json=excluded.result_json,
                updated_at=excluded.updated_at
            """,
            (
                job_name,
                period_key,
                result.status,
                run_id,
                json.dumps(result.payload),
                now,
                now,
            ),
        )
