"""Autonomous scheduler: idempotent jobs, calendar, worker lock (M5)."""

from __future__ import annotations

from typing import Any

from dividend_agent.scheduling.calendar import is_trading_day, nyse_holidays
from dividend_agent.scheduling.locks import LockBusyError, SchedulerLock

__all__ = [
    "CycleResult",
    "JobResult",
    "LockBusyError",
    "SchedulerLock",
    "SchedulerRunner",
    "is_trading_day",
    "nyse_holidays",
    "run_jobs",
]


def __getattr__(name: str) -> Any:
    # Lazy: avoid cycle with reporting.alerts → scheduling.calendar → jobs → alerts.
    if name in {"CycleResult", "run_jobs"}:
        from dividend_agent.scheduling.cycle import CycleResult, run_jobs

        return {"CycleResult": CycleResult, "run_jobs": run_jobs}[name]
    if name in {"JobResult", "SchedulerRunner"}:
        from dividend_agent.scheduling.runner import JobResult, SchedulerRunner

        return {"JobResult": JobResult, "SchedulerRunner": SchedulerRunner}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
