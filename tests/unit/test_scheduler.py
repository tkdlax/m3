"""M5 scheduler: idempotency, pause, holiday skip."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from dividend_agent.brokers.fake_paper import FakePaperBroker
from dividend_agent.config import AppConfig
from dividend_agent.scheduling.calendar import is_trading_day, nyse_holidays
from dividend_agent.scheduling.cycle import run_jobs
from dividend_agent.scheduling.jobs import JobDeps, register_default_jobs
from dividend_agent.scheduling.locks import LockBusyError, SchedulerLock
from dividend_agent.scheduling.runner import SchedulerRunner
from dividend_agent.storage import open_migrated


def test_holiday_not_trading_day() -> None:
    # Christmas 2026 is Friday
    assert date(2026, 12, 25) in nyse_holidays(2026)
    assert not is_trading_day(date(2026, 12, 25))
    assert not is_trading_day(date(2026, 12, 26))  # Saturday
    assert is_trading_day(date(2026, 12, 22))  # Tuesday


def test_trading_day_skips_holiday(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    db = tmp_path / "s.sqlite3"
    reports = tmp_path / "reports"
    cycle = run_jobs(
        config=AppConfig(),
        broker=broker,
        db_path=db,
        reports_dir=reports,
        job_names=["trading_day"],
        period_keys={"trading_day": "2026-12-25"},
        environ={
            "TRADING_PAUSED": "false",
            "ALPACA_PAPER_API_KEY": "x",
            "ALPACA_PAPER_API_SECRET": "y",
        },
    )
    assert cycle.results[0].status == "skipped_holiday"
    assert cycle.results[0].reused is False


def test_idempotent_job_runs(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    db = tmp_path / "s.sqlite3"
    reports = tmp_path / "reports"
    env = {
        "TRADING_PAUSED": "false",
        "ALPACA_PAPER_API_KEY": "x",
        "ALPACA_PAPER_API_SECRET": "y",
    }
    # Use a known trading day
    pk = "2026-12-22"
    first = run_jobs(
        config=AppConfig(),
        broker=broker,
        db_path=db,
        reports_dir=reports,
        job_names=["trading_day"],
        period_keys={"trading_day": pk},
        environ=env,
    )
    second = run_jobs(
        config=AppConfig(),
        broker=broker,
        db_path=db,
        reports_dir=reports,
        job_names=["trading_day"],
        period_keys={"trading_day": pk},
        environ=env,
    )
    assert first.results[0].status == "completed"
    assert first.results[0].reused is False
    assert second.results[0].reused is True
    assert second.results[0].status == "completed"


def test_pause_skips_trading_actions(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    db = tmp_path / "s.sqlite3"
    reports = tmp_path / "reports"
    cycle = run_jobs(
        config=AppConfig(),
        broker=broker,
        db_path=db,
        reports_dir=reports,
        job_names=["trading_day"],
        period_keys={"trading_day": "2026-12-22"},
        environ={
            "TRADING_PAUSED": "true",
            "ALPACA_PAPER_API_KEY": "x",
            "ALPACA_PAPER_API_SECRET": "y",
        },
    )
    assert cycle.results[0].status == "skipped_paused"
    assert cycle.results[0].payload.get("trading_paused") is True


def test_monthly_runs_while_paused(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    db = tmp_path / "s.sqlite3"
    reports = tmp_path / "reports"
    cycle = run_jobs(
        config=AppConfig(),
        broker=broker,
        db_path=db,
        reports_dir=reports,
        job_names=["monthly"],
        period_keys={"monthly": "2026-10"},
        environ={
            "TRADING_PAUSED": "true",
            "ALPACA_PAPER_API_KEY": "x",
            "ALPACA_PAPER_API_SECRET": "y",
        },
    )
    assert cycle.results[0].status == "completed"
    assert cycle.results[0].payload.get("trading_paused") is True
    report_path = Path(cycle.results[0].payload["report_path"])
    assert report_path.exists()


def test_startup_reconciles(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    db = tmp_path / "s.sqlite3"
    reports = tmp_path / "reports"
    cycle = run_jobs(
        config=AppConfig(),
        broker=broker,
        db_path=db,
        reports_dir=reports,
        job_names=["startup"],
        period_keys={"startup": "2026-10-30:startup"},
        environ={
            "TRADING_PAUSED": "true",
            "ALPACA_PAPER_API_KEY": "x",
            "ALPACA_PAPER_API_SECRET": "y",
        },
    )
    assert cycle.results[0].status == "completed"
    assert cycle.results[0].payload["paper_status"] == "verified_paper"


def test_worker_lock_busy(tmp_path: Path) -> None:
    conn = open_migrated(tmp_path / "lock.sqlite3")
    lock_a = SchedulerLock(conn, holder="a", ttl_seconds=600)
    lock_a.acquire()
    lock_b = SchedulerLock(conn, holder="b", ttl_seconds=600)
    with pytest.raises(LockBusyError):
        lock_b.acquire()
    lock_a.release()
    lock_b.acquire()
    lock_b.release()
    conn.close()


def test_event_review_records_alert(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    db = tmp_path / "s.sqlite3"
    reports = tmp_path / "reports"
    cycle = run_jobs(
        config=AppConfig(),
        broker=broker,
        db_path=db,
        reports_dir=reports,
        job_names=["event_review"],
        period_keys={"event_review": "fill:test-order"},
        environ={
            "TRADING_PAUSED": "true",
            "ALPACA_PAPER_API_KEY": "x",
            "ALPACA_PAPER_API_SECRET": "y",
        },
    )
    assert cycle.results[0].status == "completed"
    conn = open_migrated(db)
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM notifications").fetchone()["c"]
    finally:
        conn.close()
    assert int(n) >= 1


def test_register_and_force_rerun(tmp_path: Path) -> None:
    conn = open_migrated(tmp_path / "f.sqlite3")
    broker = FakePaperBroker()
    deps = JobDeps(
        config=AppConfig(),
        broker=broker,
        reports_dir=tmp_path / "r",
        environ={"TRADING_PAUSED": "false"},
    )
    runner = SchedulerRunner(conn)
    register_default_jobs(runner, deps)
    r1 = runner.run("trading_day", "2026-12-22")
    r2 = runner.run("trading_day", "2026-12-22", force=True)
    assert r1.reused is False
    assert r2.reused is False
    conn.close()
