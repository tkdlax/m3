"""Monthly performance / ops report (spec §15). Milestone E: period-scoped."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dividend_agent.scheduling.calendar import monthly_period_key, now_utc


@dataclass
class MonthlyReport:
    period_key: str
    account_id: str | None
    equity: str | None = None
    cash: str | None = None
    dividends_received: str = "0"
    net_option_premiums: str = "0"
    realized_stock_pnl: str = "0"
    unrealized_stock_pnl: str = "0"
    total_return_note: str = (
        "period-scoped ledger estimate; see portfolio.performance methodology; "
        "broker remains authoritative"
    )
    assignments: int = 0
    pending_approvals: int = 0
    skipped_opportunities: list[str] = field(default_factory=list)
    research_freshness: dict[str, Any] = field(default_factory=dict)
    period_filter: str = ""
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _period_bounds(period_key: str) -> tuple[str, str]:
    """Return [start, end) ISO date strings for YYYY-MM period keys."""
    year_s, _, month_s = period_key.partition("-")
    year, month = int(year_s), int(month_s)
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year + 1:04d}-01-01" if month == 12 else f"{year:04d}-{month + 1:02d}-01"
    return start, end


def build_monthly_report(
    conn: sqlite3.Connection,
    *,
    account_id: str | None = None,
    period_key: str | None = None,
) -> MonthlyReport:
    period = period_key or monthly_period_key()
    start, end = _period_bounds(period) if len(period) >= 7 and period[4] == "-" else ("", "")
    equity = cash = None
    if account_id:
        row = conn.execute(
            "SELECT equity, cash FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        if row:
            equity, cash = row["equity"], row["cash"]

    from dividend_agent.portfolio.performance import compute_performance

    perf = compute_performance(conn, account_id=account_id, start=start, end=end)
    assignments = _count_activities(
        conn, account_id, ("OPASN", "ASSIGN", "EXERCISE"), start=start, end=end
    )
    # Prefer persisted corporate_action_events when present
    ca_assignments = _count_corporate_actions(conn, account_id, start=start, end=end)
    if ca_assignments:
        assignments = max(assignments, ca_assignments)
    pending = _count_pending_approvals(conn)
    research = _research_freshness(conn, start=start, end=end)
    skipped = _recent_skips(conn, period_key=period)

    return MonthlyReport(
        period_key=period,
        account_id=account_id,
        equity=equity,
        cash=cash,
        dividends_received=str(perf.dividends),
        net_option_premiums=str(perf.net_option_premiums),
        realized_stock_pnl=str(perf.realized_stock_pnl),
        unrealized_stock_pnl=str(perf.unrealized_stock_pnl),
        total_return_note=perf.methodology + (f"; notes={list(perf.notes)}" if perf.notes else ""),
        assignments=assignments,
        pending_approvals=pending,
        skipped_opportunities=skipped,
        research_freshness=research,
        period_filter=f"[{start},{end})" if start else "unscoped",
        generated_at=now_utc().replace(microsecond=0).isoformat(),
    )


def write_monthly_report(report: MonthlyReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"monthly-report-{report.period_key}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def _count_activities(
    conn: sqlite3.Connection,
    account_id: str | None,
    types: tuple[str, ...],
    *,
    start: str = "",
    end: str = "",
) -> int:
    if not account_id:
        return 0
    placeholders = ",".join("?" * len(types))
    rows = conn.execute(
        f"""
        SELECT transaction_time FROM activities
        WHERE account_id = ? AND activity_type IN ({placeholders})
        """,
        (account_id, *types),
    ).fetchall()
    if not start:
        return len(rows)
    return sum(1 for r in rows if _in_period(r["transaction_time"], start, end))


def _count_corporate_actions(
    conn: sqlite3.Connection,
    account_id: str | None,
    *,
    start: str = "",
    end: str = "",
) -> int:
    if not account_id:
        return 0
    try:
        rows = conn.execute(
            """
            SELECT transaction_time, activity_type FROM corporate_action_events
            WHERE account_id = ? AND activity_type IN ('OPASN', 'ASSIGN', 'EXERCISE')
            """,
            (account_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    if not start:
        return len(rows)
    return sum(1 for r in rows if _in_period(r["transaction_time"], start, end))


def _count_pending_approvals(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM approvals WHERE status = 'pending'"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["c"]) if row else 0


def _research_freshness(
    conn: sqlite3.Connection, *, start: str = "", end: str = ""
) -> dict[str, Any]:
    try:
        rows = conn.execute(
            "SELECT created_at FROM research_memos ORDER BY created_at DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return {"memos": 0, "latest": None, "period_memos": 0}
    period_n = 0
    if start and end:
        period_n = sum(1 for r in rows if _in_period(r["created_at"], start, end))
    return {
        "memos": len(rows),
        "latest": rows[0]["created_at"] if rows else None,
        "period_memos": period_n,
    }


def _recent_skips(conn: sqlite3.Connection, *, period_key: str) -> list[str]:
    try:
        rows = conn.execute(
            """
            SELECT detail, result_json FROM job_keys
            WHERE job_name = 'monthly' AND period_key = ?
            """,
            (period_key,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[str] = []
    for r in rows:
        try:
            payload = json.loads(r["result_json"] or "{}")
            skipped = payload.get("call_selection", {}).get("skipped") or []
            out.extend(str(s) for s in skipped[:20])
        except json.JSONDecodeError:
            if r["detail"] and "skip" in str(r["detail"]).lower():
                out.append(str(r["detail"]))
    return out[:20]


def _in_period(value: Any, start: str, end: str) -> bool:
    if not value:
        return False
    text = str(value)[:10]
    return start <= text < end
