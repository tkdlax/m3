"""High-level scheduler cycles under a single worker lock."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dividend_agent.brokers.interface import BrokerAdapter
from dividend_agent.config import AppConfig
from dividend_agent.scheduling.jobs import JobDeps, default_period_for, register_default_jobs
from dividend_agent.scheduling.locks import SchedulerLock
from dividend_agent.scheduling.runner import JobResult, SchedulerRunner
from dividend_agent.storage.db import open_migrated


@dataclass
class CycleResult:
    results: list[JobResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [
                {
                    "job_name": r.job_name,
                    "period_key": r.period_key,
                    "status": r.status,
                    "detail": r.detail,
                    "reused": r.reused,
                    "payload": r.payload,
                }
                for r in self.results
            ]
        }


def run_jobs(
    *,
    config: AppConfig,
    broker: BrokerAdapter,
    db_path: Path,
    reports_dir: Path,
    job_names: list[str],
    environ: dict[str, str] | None = None,
    force: bool = False,
    period_keys: dict[str, str] | None = None,
    research_symbols: tuple[str, ...] = (),
    option_chain_path: Path | None = None,
) -> CycleResult:
    conn = open_migrated(db_path)
    results: list[JobResult] = []
    try:
        with SchedulerLock(conn):
            runner = SchedulerRunner(conn)
            deps = JobDeps(
                config=config,
                broker=broker,
                reports_dir=reports_dir,
                environ=environ,
                research_symbols=research_symbols,
                option_chain_path=option_chain_path,
            )
            register_default_jobs(runner, deps)
            for name in job_names:
                pk = (period_keys or {}).get(name) or default_period_for(name)
                results.append(runner.run(name, pk, force=force))
    finally:
        conn.close()
        close = getattr(broker, "close", None)
        if callable(close):
            close()
    return CycleResult(results=results)


def format_cycle_json(cycle: CycleResult) -> str:
    return json.dumps(cycle.to_dict(), indent=2)
