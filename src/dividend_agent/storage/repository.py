"""Ledger repository — persist broker snapshots and audit events."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from dividend_agent.brokers.types import (
    AccountSnapshot,
    ActivitySnapshot,
    OrderSnapshot,
    PositionSnapshot,
)
from dividend_agent.storage.models import HoldingRow, JobRunRow


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def _d(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


class LedgerRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def upsert_account(self, account: AccountSnapshot) -> None:
        synced = _now()
        self._conn.execute(
            """
            INSERT INTO accounts (
                account_id, status, currency, cash, buying_power, equity,
                portfolio_value, pattern_day_trader, trading_blocked,
                account_blocked, options_approved_level, raw_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                status=excluded.status,
                currency=excluded.currency,
                cash=excluded.cash,
                buying_power=excluded.buying_power,
                equity=excluded.equity,
                portfolio_value=excluded.portfolio_value,
                pattern_day_trader=excluded.pattern_day_trader,
                trading_blocked=excluded.trading_blocked,
                account_blocked=excluded.account_blocked,
                options_approved_level=excluded.options_approved_level,
                raw_json=excluded.raw_json,
                synced_at=excluded.synced_at
            """,
            (
                account.account_id,
                account.status,
                account.currency,
                str(account.cash),
                str(account.buying_power),
                str(account.equity),
                str(account.portfolio_value),
                int(account.pattern_day_trader),
                int(account.trading_blocked),
                int(account.account_blocked),
                account.options_approved_level,
                json.dumps(account.raw),
                synced,
            ),
        )

    def replace_holdings(self, account_id: str, positions: list[PositionSnapshot]) -> None:
        synced = _now()
        self._conn.execute("DELETE FROM holdings WHERE account_id = ?", (account_id,))
        self._conn.execute("DELETE FROM lots WHERE account_id = ?", (account_id,))
        for pos in positions:
            self._conn.execute(
                """
                INSERT INTO holdings (
                    account_id, symbol, asset_class, qty, qty_available,
                    avg_entry_price, market_value, cost_basis, side, asset_id,
                    raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    pos.symbol,
                    pos.asset_class,
                    str(pos.qty),
                    _d(pos.qty_available),
                    str(pos.avg_entry_price),
                    _d(pos.market_value),
                    _d(pos.cost_basis),
                    pos.side,
                    pos.asset_id,
                    json.dumps(pos.raw),
                    synced,
                ),
            )
            # M1 lot approximation: single lot from broker avg entry (tax lots later).
            self._conn.execute(
                """
                INSERT INTO lots (
                    account_id, symbol, qty, cost_per_share, source, synced_at
                ) VALUES (?, ?, ?, ?, 'broker_avg_entry', ?)
                """,
                (account_id, pos.symbol, str(pos.qty), str(pos.avg_entry_price), synced),
            )

    def upsert_orders(self, account_id: str, orders: list[OrderSnapshot]) -> None:
        synced = _now()
        for order in orders:
            self._conn.execute(
                """
                INSERT INTO orders (
                    broker_order_id, account_id, client_order_id, symbol, side,
                    order_type, status, qty, filled_qty, limit_price,
                    filled_avg_price, asset_class, submitted_at, updated_at,
                    raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(broker_order_id) DO UPDATE SET
                    client_order_id=excluded.client_order_id,
                    symbol=excluded.symbol,
                    side=excluded.side,
                    order_type=excluded.order_type,
                    status=excluded.status,
                    qty=excluded.qty,
                    filled_qty=excluded.filled_qty,
                    limit_price=excluded.limit_price,
                    filled_avg_price=excluded.filled_avg_price,
                    asset_class=excluded.asset_class,
                    submitted_at=excluded.submitted_at,
                    updated_at=excluded.updated_at,
                    raw_json=excluded.raw_json,
                    synced_at=excluded.synced_at
                """,
                (
                    order.broker_order_id,
                    account_id,
                    order.client_order_id,
                    order.symbol,
                    order.side,
                    order.order_type,
                    order.status,
                    _d(order.qty),
                    _d(order.filled_qty),
                    _d(order.limit_price),
                    _d(order.filled_avg_price),
                    order.asset_class,
                    _dt(order.submitted_at),
                    _dt(order.updated_at),
                    json.dumps(order.raw),
                    synced,
                ),
            )

    def upsert_activities(self, account_id: str, activities: list[ActivitySnapshot]) -> None:
        synced = _now()
        for act in activities:
            if not act.activity_id:
                continue
            self._conn.execute(
                """
                INSERT INTO activities (
                    activity_id, account_id, activity_type, symbol, side, qty,
                    price, net_amount, transaction_time, order_id, raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(activity_id) DO UPDATE SET
                    activity_type=excluded.activity_type,
                    symbol=excluded.symbol,
                    side=excluded.side,
                    qty=excluded.qty,
                    price=excluded.price,
                    net_amount=excluded.net_amount,
                    transaction_time=excluded.transaction_time,
                    order_id=excluded.order_id,
                    raw_json=excluded.raw_json,
                    synced_at=excluded.synced_at
                """,
                (
                    act.activity_id,
                    account_id,
                    act.activity_type,
                    act.symbol,
                    act.side,
                    _d(act.qty),
                    _d(act.price),
                    _d(act.net_amount),
                    _dt(act.transaction_time),
                    act.order_id,
                    json.dumps(act.raw),
                    synced,
                ),
            )

    def start_job(self, job_name: str, account_id: str | None = None) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO job_runs (job_name, status, started_at, account_id)
            VALUES (?, 'running', ?, ?)
            """,
            (job_name, _now(), account_id),
        )
        row_id = cur.lastrowid
        if row_id is None:
            raise RuntimeError("failed to allocate job_runs id")
        return int(row_id)

    def finish_job(self, job_id: int, *, status: str, detail: str = "") -> None:
        self._conn.execute(
            """
            UPDATE job_runs
            SET status = ?, finished_at = ?, detail = ?
            WHERE id = ?
            """,
            (status, _now(), detail, job_id),
        )

    def append_audit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        account_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO audit_events (
                event_type, created_at, account_id, payload_json, correlation_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_type,
                _now(),
                account_id,
                json.dumps(payload),
                correlation_id,
            ),
        )

    def get_account(self, account_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._conn.execute(
            "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        return row

    def list_holdings(self, account_id: str) -> list[HoldingRow]:
        rows = self._conn.execute(
            "SELECT * FROM holdings WHERE account_id = ? ORDER BY symbol",
            (account_id,),
        ).fetchall()
        return [
            HoldingRow(
                account_id=r["account_id"],
                symbol=r["symbol"],
                qty=Decimal(r["qty"]),
                avg_entry_price=Decimal(r["avg_entry_price"]),
                asset_class=r["asset_class"],
            )
            for r in rows
        ]

    def count_orders(self, account_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE account_id = ?", (account_id,)
        ).fetchone()
        return int(row["c"]) if row else 0

    def count_activities(self, account_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM activities WHERE account_id = ?", (account_id,)
        ).fetchone()
        return int(row["c"]) if row else 0

    def latest_job(self, job_name: str) -> JobRunRow | None:
        row = self._conn.execute(
            """
            SELECT * FROM job_runs WHERE job_name = ?
            ORDER BY id DESC LIMIT 1
            """,
            (job_name,),
        ).fetchone()
        if row is None:
            return None
        return JobRunRow(
            id=row["id"],
            job_name=row["job_name"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            detail=row["detail"],
            account_id=row["account_id"],
        )

    def list_accounts(self) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM accounts ORDER BY account_id"))
