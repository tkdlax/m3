"""Monthly performance / ops report (spec §15)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from decimal import Decimal
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
    total_return_note: str = "ledger-derived estimate; broker remains authoritative"
    assignments: int = 0
    pending_approvals: int = 0
    skipped_opportunities: list[str] = field(default_factory=list)
    research_freshness: dict[str, Any] = field(default_factory=dict)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_monthly_report(
    conn: sqlite3.Connection,
    *,
    account_id: str | None = None,
    period_key: str | None = None,
) -> MonthlyReport:
    period = period_key or monthly_period_key()
    equity = cash = None
    if account_id:
        row = conn.execute(
            "SELECT equity, cash FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        if row:
            equity, cash = row["equity"], row["cash"]

    dividends = _sum_activity_amount(conn, account_id, ("DIV", "DIVNRA", "DIVFEE"))
    premiums = _sum_option_premiums(conn, account_id)
    assignments = _count_activities(conn, account_id, ("OPASN", "ASSIGN", "EXERCISE"))
    pending = _count_pending_approvals(conn)
    research = _research_freshness(conn)
    skipped = _recent_skips(conn)

    return MonthlyReport(
        period_key=period,
        account_id=account_id,
        equity=equity,
        cash=cash,
        dividends_received=str(dividends),
        net_option_premiums=str(premiums),
        assignments=assignments,
        pending_approvals=pending,
        skipped_opportunities=skipped,
        research_freshness=research,
        generated_at=now_utc().replace(microsecond=0).isoformat(),
    )


def write_monthly_report(report: MonthlyReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"monthly-report-{report.period_key}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def _sum_activity_amount(
    conn: sqlite3.Connection,
    account_id: str | None,
    types: tuple[str, ...],
) -> Decimal:
    if not account_id:
        return Decimal("0")
    placeholders = ",".join("?" * len(types))
    row = conn.execute(
        f"""
        SELECT net_amount FROM activities
        WHERE account_id = ? AND activity_type IN ({placeholders})
        """,
        (account_id, *types),
    ).fetchall()
    total = Decimal("0")
    for r in row:
        if r["net_amount"]:
            total += Decimal(str(r["net_amount"]))
    return total


def _sum_option_premiums(conn: sqlite3.Connection, account_id: str | None) -> Decimal:
    if not account_id:
        return Decimal("0")
    # Approximate: filled sell option orders * filled_avg_price * 100
    rows = conn.execute(
        """
        SELECT filled_qty, filled_avg_price, side, asset_class, symbol
        FROM orders
        WHERE account_id = ? AND status = 'filled'
        """,
        (account_id,),
    ).fetchall()
    total = Decimal("0")
    for r in rows:
        asset = (r["asset_class"] or "").lower()
        sym = r["symbol"] or ""
        if "option" not in asset and len(sym) < 15:
            continue
        qty = Decimal(str(r["filled_qty"] or "0"))
        px = Decimal(str(r["filled_avg_price"] or "0"))
        prem = qty * px * Decimal("100")
        if (r["side"] or "").lower() == "sell":
            total += prem
        else:
            total -= prem
    return total


def _count_activities(
    conn: sqlite3.Connection, account_id: str | None, types: tuple[str, ...]
) -> int:
    if not account_id:
        return 0
    placeholders = ",".join("?" * len(types))
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM activities
        WHERE account_id = ? AND activity_type IN ({placeholders})
        """,
        (account_id, *types),
    ).fetchone()
    return int(row["c"]) if row else 0


def _count_pending_approvals(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM approvals WHERE status = 'pending'"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["c"]) if row else 0


def _research_freshness(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c, MAX(created_at) AS latest
            FROM research_memos
            """
        ).fetchone()
    except sqlite3.OperationalError:
        return {"memos": 0, "latest": None}
    return {"memos": int(row["c"]) if row else 0, "latest": row["latest"] if row else None}


def _recent_skips(conn: sqlite3.Connection) -> list[str]:
    try:
        rows = conn.execute(
            """
            SELECT detail FROM job_runs
            WHERE job_name LIKE 'monthly%' AND detail LIKE '%skip%'
            ORDER BY id DESC LIMIT 20
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [str(r["detail"]) for r in rows if r["detail"]]
