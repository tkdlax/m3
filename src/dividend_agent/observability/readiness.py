"""Operational readiness beyond process liveness (Milestone F).

Docker HEALTHCHECK must use ``health --liveness`` (process-only). Operational
``health`` (default when a DB path is available) and ``health --readiness``
report degraded/not_ready when scheduled work is failing — never ``ok`` then.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dividend_agent import __version__
from dividend_agent.config import AppConfig
from dividend_agent.domain.models import OperatingMode
from dividend_agent.execution.kill_switch import is_trading_paused
from dividend_agent.storage.db import current_schema_version, open_migrated


@dataclass
class ReadinessCheck:
    name: str
    status: str  # pass | warn | fail
    detail: str = ""


@dataclass
class ReadinessReport:
    status: str  # ready | degraded | not_ready
    version: str
    mode: str
    trading_paused: bool
    paper_only: bool = True
    live_trading: bool = False
    checks: list[ReadinessCheck] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "version": self.version,
            "mode": self.mode,
            "trading_paused": self.trading_paused,
            "paper_only": self.paper_only,
            "live_trading": self.live_trading,
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail} for c in self.checks
            ],
            "generated_at": self.generated_at,
        }


def assess_readiness(
    *,
    config: AppConfig | None = None,
    db_path: str | Path | None = None,
    environ: dict[str, str] | None = None,
    stale_after_hours: float = 36.0,
    check_broker: bool = False,
) -> ReadinessReport:
    """Build a readiness report. Never contacts brokers unless ``check_broker``."""
    mode = config.mode.value if config else OperatingMode.PAPER_DRY_RUN.value
    paused = is_trading_paused(environ=environ)
    checks: list[ReadinessCheck] = []
    now = datetime.now(UTC)

    path = (
        Path(db_path)
        if db_path
        else Path((environ or os.environ).get("DB_PATH", "/data/agent.sqlite3"))
    )
    conn: sqlite3.Connection | None = None
    try:
        conn = open_migrated(path)
        version = current_schema_version(conn)
        checks.append(
            ReadinessCheck(
                name="database",
                status="pass",
                detail=f"schema_version={version} path={path}",
            )
        )
        _job_checks(conn, checks, now=now, stale_after_hours=stale_after_hours)
        _cycle_checks(conn, checks)
        _approval_checks(conn, checks)
        _assignment_checks(conn, checks)
    except Exception as exc:  # noqa: BLE001
        checks.append(ReadinessCheck(name="database", status="fail", detail=str(exc)))
    finally:
        if conn is not None:
            conn.close()

    if check_broker:
        checks.append(_broker_check(environ))

    checks.append(
        ReadinessCheck(
            name="mode_defaults",
            status="pass" if mode == OperatingMode.PAPER_DRY_RUN.value or paused else "warn",
            detail=f"mode={mode} trading_paused={paused} paper_auto_gated=true",
        )
    )

    statuses = {c.status for c in checks}
    if "fail" in statuses:
        overall = "not_ready"
    elif "warn" in statuses:
        overall = "degraded"
    else:
        overall = "ready"

    return ReadinessReport(
        status=overall,
        version=__version__,
        mode=mode,
        trading_paused=paused,
        checks=checks,
        generated_at=now.replace(microsecond=0).isoformat(),
    )


def _job_checks(
    conn: sqlite3.Connection,
    checks: list[ReadinessCheck],
    *,
    now: datetime,
    stale_after_hours: float,
) -> None:
    # Recent failures in job_runs (24h) → fail (scheduled work failing).
    try:
        failed = conn.execute(
            """
            SELECT job_name, detail, finished_at FROM job_runs
            WHERE status = 'failed'
            ORDER BY id DESC LIMIT 10
            """
        ).fetchall()
    except sqlite3.OperationalError as exc:
        checks.append(ReadinessCheck(name="job_runs", status="fail", detail=str(exc)))
        return

    recent_fail = None
    for row in failed:
        finished = row["finished_at"]
        if not finished:
            recent_fail = row
            break
        try:
            ft = datetime.fromisoformat(str(finished))
            if ft.tzinfo is None:
                ft = ft.replace(tzinfo=UTC)
            if now - ft <= timedelta(hours=24):
                recent_fail = row
                break
        except ValueError:
            recent_fail = row
            break

    if recent_fail is not None:
        detail = str(recent_fail["detail"] or "")[:120]
        checks.append(
            ReadinessCheck(
                name="recent_job_failures",
                status="fail",
                detail=f"{recent_fail['job_name']}: {detail}",
            )
        )
    else:
        checks.append(ReadinessCheck(name="recent_job_failures", status="pass", detail="none"))

    # Also inspect job_keys with status=failed
    try:
        failed_keys = conn.execute(
            """
            SELECT job_name, period_key, updated_at FROM job_keys
            WHERE status = 'failed'
            ORDER BY updated_at DESC LIMIT 5
            """
        ).fetchall()
    except sqlite3.OperationalError:
        failed_keys = []
    if failed_keys:
        latest = failed_keys[0]
        checks.append(
            ReadinessCheck(
                name="failed_job_keys",
                status="fail",
                detail=f"{latest['job_name']}@{latest['period_key']}",
            )
        )
    else:
        checks.append(ReadinessCheck(name="failed_job_keys", status="pass", detail="none"))

    try:
        keys = conn.execute(
            """
            SELECT job_name, period_key, status, updated_at FROM job_keys
            WHERE status IN ('completed', 'skipped', 'skipped_holiday', 'skipped_paused')
            ORDER BY updated_at DESC LIMIT 20
            """
        ).fetchall()
    except sqlite3.OperationalError:
        checks.append(ReadinessCheck(name="job_freshness", status="warn", detail="no job_keys"))
        return

    if not keys:
        checks.append(
            ReadinessCheck(
                name="job_freshness",
                status="warn",
                detail="no completed jobs yet — scheduler may not have run",
            )
        )
        return

    newest = keys[0]["updated_at"]
    try:
        updated = datetime.fromisoformat(str(newest))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        age = now - updated
        if age > timedelta(hours=stale_after_hours):
            checks.append(
                ReadinessCheck(
                    name="job_freshness",
                    status="fail",
                    detail=(
                        f"last success {newest} "
                        f"age_hours={age.total_seconds() / 3600:.1f} "
                        f"(stale>{stale_after_hours}h)"
                    ),
                )
            )
        else:
            checks.append(
                ReadinessCheck(
                    name="job_freshness",
                    status="pass",
                    detail=f"last={keys[0]['job_name']}@{newest}",
                )
            )
    except ValueError:
        checks.append(
            ReadinessCheck(name="job_freshness", status="warn", detail=f"unparsed:{newest}")
        )


def _cycle_checks(conn: sqlite3.Connection, checks: list[ReadinessCheck]) -> None:
    try:
        open_cycles = conn.execute(
            """
            SELECT COUNT(*) AS c FROM paper_cycles
            WHERE status IN ('in_progress', 'awaiting_fill', 'awaiting_approval', 'dry_run_hold')
            """
        ).fetchone()
        blocked = conn.execute(
            """
            SELECT COUNT(*) AS c FROM paper_cycles WHERE status = 'blocked'
            """
        ).fetchone()
        failed = conn.execute(
            """
            SELECT COUNT(*) AS c FROM paper_cycles
            WHERE status IN ('failed', 'error')
            """
        ).fetchone()
        # Stale in-progress cycles (>48h without update)
        stale = conn.execute(
            """
            SELECT COUNT(*) AS c FROM paper_cycles
            WHERE status IN ('in_progress', 'awaiting_fill')
              AND updated_at < ?
            """,
            ((datetime.now(UTC) - timedelta(hours=48)).replace(microsecond=0).isoformat(),),
        ).fetchone()
    except sqlite3.OperationalError:
        checks.append(ReadinessCheck(name="paper_cycles", status="pass", detail="table_absent"))
        return
    open_n = int(open_cycles["c"]) if open_cycles else 0
    blocked_n = int(blocked["c"]) if blocked else 0
    failed_n = int(failed["c"]) if failed else 0
    stale_n = int(stale["c"]) if stale else 0
    status = "fail" if failed_n or blocked_n or stale_n else "pass"
    checks.append(
        ReadinessCheck(
            name="paper_cycles",
            status=status,
            detail=f"open={open_n} blocked={blocked_n} failed={failed_n} stale={stale_n}",
        )
    )


def _approval_checks(conn: sqlite3.Connection, checks: list[ReadinessCheck]) -> None:
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM approvals WHERE status = 'pending'"
        ).fetchone()
    except sqlite3.OperationalError:
        checks.append(ReadinessCheck(name="approvals", status="pass", detail="table_absent"))
        return
    pending = int(row["c"]) if row else 0
    checks.append(
        ReadinessCheck(
            name="approvals",
            status="warn" if pending else "pass",
            detail=f"pending={pending}",
        )
    )


def _assignment_checks(conn: sqlite3.Connection, checks: list[ReadinessCheck]) -> None:
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM corporate_action_events WHERE handled = 0"
        ).fetchone()
    except sqlite3.OperationalError:
        checks.append(
            ReadinessCheck(name="assignments", status="pass", detail="monitor_not_migrated")
        )
        return
    unhandled = int(row["c"]) if row else 0
    checks.append(
        ReadinessCheck(
            name="assignments",
            status="warn" if unhandled else "pass",
            detail=f"unhandled={unhandled}",
        )
    )


def _broker_check(environ: dict[str, str] | None) -> ReadinessCheck:
    env = environ if environ is not None else dict(os.environ)
    if not (env.get("ALPACA_PAPER_API_KEY") and env.get("ALPACA_PAPER_API_SECRET")):
        return ReadinessCheck(
            name="broker",
            status="warn",
            detail="paper credentials absent — dry-run/research only",
        )
    try:
        from dividend_agent.brokers.factory import create_broker_adapter

        adapter = create_broker_adapter(environ=env)
        result = adapter.verify_paper()
        close = getattr(adapter, "close", None)
        if callable(close):
            close()
        if result.is_verified_paper:
            return ReadinessCheck(name="broker", status="pass", detail="verified_paper")
        return ReadinessCheck(
            name="broker", status="warn", detail=result.detail or result.status.value
        )
    except Exception as exc:  # noqa: BLE001
        return ReadinessCheck(name="broker", status="fail", detail=str(exc))
