"""Return reporting: dividends, premiums, realized/unrealized stock P&L.

Methodology (documented for operators / PROJECT_SPEC §2.7):
- **Dividends**: sum of broker activity net_amount for DIV* types in period.
- **Net option premiums**: filled option orders (sell − buy) × 100 × price in period.
- **Unrealized stock P&L**: equity holdings where market_value and cost_basis
  (or qty × avg_entry) are present: market_value − cost. Options excluded.
- **Realized stock P&L**: filled equity *sell* orders in period using
  (filled_avg_price − avg_entry_at_sync) × filled_qty when a matching holding
  lot cost exists; otherwise use activity FILL/SELL net_amount when present.
- Broker remains authoritative; these are ledger estimates from last reconcile.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class PerformanceSnapshot:
    dividends: Decimal
    net_option_premiums: Decimal
    realized_stock_pnl: Decimal
    unrealized_stock_pnl: Decimal
    methodology: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "dividends": str(self.dividends),
            "net_option_premiums": str(self.net_option_premiums),
            "realized_stock_pnl": str(self.realized_stock_pnl),
            "unrealized_stock_pnl": str(self.unrealized_stock_pnl),
            "methodology": self.methodology,
            "notes": list(self.notes),
        }


_METHODOLOGY = (
    "ledger estimate from holdings + filled orders + activities; "
    "period-scoped where start/end provided; broker remains authoritative"
)


def compute_performance(
    conn: sqlite3.Connection,
    *,
    account_id: str | None,
    start: str = "",
    end: str = "",
) -> PerformanceSnapshot:
    notes: list[str] = []
    if not account_id:
        return PerformanceSnapshot(
            dividends=Decimal("0"),
            net_option_premiums=Decimal("0"),
            realized_stock_pnl=Decimal("0"),
            unrealized_stock_pnl=Decimal("0"),
            methodology=_METHODOLOGY,
            notes=("no_account_id",),
        )

    dividends = _sum_activity_amount(conn, account_id, ("DIV", "DIVNRA", "DIVFEE"), start, end)
    premiums = _sum_option_premiums(conn, account_id, start, end)
    unrealized, u_notes = _unrealized_stock_pnl(conn, account_id)
    notes.extend(u_notes)
    realized, r_notes = _realized_stock_pnl(conn, account_id, start, end)
    notes.extend(r_notes)

    return PerformanceSnapshot(
        dividends=dividends,
        net_option_premiums=premiums,
        realized_stock_pnl=realized,
        unrealized_stock_pnl=unrealized,
        methodology=_METHODOLOGY,
        notes=tuple(notes),
    )


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def _in_period(value: Any, start: str, end: str) -> bool:
    if not start or not end or not value:
        return not start  # unscoped → include all
    text = str(value)[:10]
    return start <= text < end


def _is_option_row(*, asset_class: str | None, symbol: str | None) -> bool:
    asset = (asset_class or "").lower()
    sym = symbol or ""
    return "option" in asset or len(sym) >= 15


def _sum_activity_amount(
    conn: sqlite3.Connection,
    account_id: str,
    types: tuple[str, ...],
    start: str,
    end: str,
) -> Decimal:
    placeholders = ",".join("?" * len(types))
    rows = conn.execute(
        f"""
        SELECT net_amount, transaction_time FROM activities
        WHERE account_id = ? AND activity_type IN ({placeholders})
        """,
        (account_id, *types),
    ).fetchall()
    total = Decimal("0")
    for r in rows:
        if start and end and not _in_period(r["transaction_time"], start, end):
            continue
        amt = _dec(r["net_amount"])
        if amt is not None:
            total += amt
    return total


def _sum_option_premiums(
    conn: sqlite3.Connection, account_id: str, start: str, end: str
) -> Decimal:
    rows = conn.execute(
        """
        SELECT filled_qty, filled_avg_price, side, asset_class, symbol,
               submitted_at, updated_at
        FROM orders
        WHERE account_id = ? AND status = 'filled'
        """,
        (account_id,),
    ).fetchall()
    total = Decimal("0")
    for r in rows:
        ts = r["updated_at"] or r["submitted_at"]
        if start and end and not _in_period(ts, start, end):
            continue
        if not _is_option_row(asset_class=r["asset_class"], symbol=r["symbol"]):
            continue
        qty = _dec(r["filled_qty"]) or Decimal("0")
        px = _dec(r["filled_avg_price"]) or Decimal("0")
        prem = qty * px * Decimal("100")
        if (r["side"] or "").lower() == "sell":
            total += prem
        else:
            total -= prem
    return total


def _unrealized_stock_pnl(conn: sqlite3.Connection, account_id: str) -> tuple[Decimal, list[str]]:
    rows = conn.execute(
        """
        SELECT symbol, asset_class, qty, avg_entry_price, market_value, cost_basis
        FROM holdings WHERE account_id = ?
        """,
        (account_id,),
    ).fetchall()
    total = Decimal("0")
    notes: list[str] = []
    used = 0
    for r in rows:
        if _is_option_row(asset_class=r["asset_class"], symbol=r["symbol"]):
            continue
        mv = _dec(r["market_value"])
        cb = _dec(r["cost_basis"])
        if cb is None:
            qty = _dec(r["qty"])
            entry = _dec(r["avg_entry_price"])
            if qty is not None and entry is not None:
                cb = qty * entry
        if mv is None or cb is None:
            notes.append(f"unrealized_skipped:{r['symbol']}:missing_mv_or_cost")
            continue
        total += mv - cb
        used += 1
    if used == 0 and not notes:
        notes.append("unrealized_no_equity_holdings")
    return total, notes


def _realized_stock_pnl(
    conn: sqlite3.Connection, account_id: str, start: str, end: str
) -> tuple[Decimal, list[str]]:
    """Estimate realized P&L from equity sells using current WAC as cost proxy.

    When the position is gone (assigned/sold out), fall back to activity net_amount
    for FILL/SELL if present. Cost proxy is imperfect after partial sells — noted.
    """
    notes: list[str] = ["realized_uses_avg_entry_cost_proxy"]
    # Map symbol → avg_entry from lots/holdings (best available cost)
    costs: dict[str, Decimal] = {}
    for r in conn.execute(
        "SELECT symbol, cost_per_share FROM lots WHERE account_id = ?",
        (account_id,),
    ).fetchall():
        cps = _dec(r["cost_per_share"])
        if cps is not None:
            costs[str(r["symbol"])] = cps
    for r in conn.execute(
        "SELECT symbol, avg_entry_price, asset_class FROM holdings WHERE account_id = ?",
        (account_id,),
    ).fetchall():
        if _is_option_row(asset_class=r["asset_class"], symbol=r["symbol"]):
            continue
        if str(r["symbol"]) not in costs:
            entry = _dec(r["avg_entry_price"])
            if entry is not None:
                costs[str(r["symbol"])] = entry

    total = Decimal("0")
    rows = conn.execute(
        """
        SELECT symbol, side, filled_qty, filled_avg_price, asset_class,
               submitted_at, updated_at
        FROM orders
        WHERE account_id = ? AND status = 'filled' AND lower(side) = 'sell'
        """,
        (account_id,),
    ).fetchall()
    matched = 0
    for r in rows:
        ts = r["updated_at"] or r["submitted_at"]
        if start and end and not _in_period(ts, start, end):
            continue
        if _is_option_row(asset_class=r["asset_class"], symbol=r["symbol"]):
            continue
        qty = _dec(r["filled_qty"]) or Decimal("0")
        px = _dec(r["filled_avg_price"]) or Decimal("0")
        cost = costs.get(str(r["symbol"]))
        if cost is None:
            notes.append(f"realized_skipped:{r['symbol']}:no_cost_basis")
            continue
        total += (px - cost) * qty
        matched += 1

    # Activity net_amount fallback when no sell orders matched but SELL/FILL exist
    if matched == 0:
        act_total = Decimal("0")
        acts = conn.execute(
            """
            SELECT net_amount, transaction_time, activity_type, symbol
            FROM activities
            WHERE account_id = ? AND upper(activity_type) IN ('FILL', 'SELL', 'CSD')
            """,
            (account_id,),
        ).fetchall()
        for a in acts:
            if start and end and not _in_period(a["transaction_time"], start, end):
                continue
            amt = _dec(a["net_amount"])
            if amt is not None:
                act_total += amt
                matched += 1
        if matched:
            notes.append("realized_from_activity_net_amount_fallback")
            total = act_total

    return total, notes
