"""Milestone F: P&L fields when data exists; operational health ≠ ok on failures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from dividend_agent.app import health_payload, readiness_payload
from dividend_agent.config import AppConfig
from dividend_agent.portfolio.performance import compute_performance
from dividend_agent.reporting.monthly import build_monthly_report
from dividend_agent.storage import open_migrated


def _seed_account(conn, account_id: str = "paper-1") -> None:
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    conn.execute(
        """
        INSERT INTO accounts (
            account_id, status, currency, cash, buying_power, equity,
            portfolio_value, pattern_day_trader, trading_blocked,
            account_blocked, options_approved_level, raw_json, synced_at
        ) VALUES (?, 'ACTIVE', 'USD', '1000', '1000', '5000', '5000', 0, 0, 0, 2, '{}', ?)
        """,
        (account_id, now),
    )
    conn.execute(
        """
        INSERT INTO holdings (
            account_id, symbol, asset_class, qty, qty_available,
            avg_entry_price, market_value, cost_basis, side, asset_id,
            raw_json, synced_at
        ) VALUES (?, 'GIS', 'us_equity', '100', '100', '40', '4500', '4000', 'long', NULL, '{}', ?)
        """,
        (account_id, now),
    )
    conn.execute(
        """
        INSERT INTO lots (account_id, symbol, qty, cost_per_share, source, synced_at)
        VALUES (?, 'GIS', '100', '40', 'broker_avg_entry', ?)
        """,
        (account_id, now),
    )
    # Dividend in period
    conn.execute(
        """
        INSERT INTO activities (
            activity_id, account_id, activity_type, symbol, side, qty, price,
            net_amount, transaction_time, order_id, raw_json, synced_at
        ) VALUES ('div-1', ?, 'DIV', 'GIS', NULL, NULL, NULL, '55.25', '2026-10-15T00:00:00+00:00',
                  NULL, '{}', ?)
        """,
        (account_id, now),
    )
    # Option premium sell fill in period
    conn.execute(
        """
        INSERT INTO orders (
            broker_order_id, account_id, client_order_id, symbol, side, order_type,
            status, qty, filled_qty, limit_price, filled_avg_price, asset_class,
            submitted_at, updated_at, raw_json, synced_at
        ) VALUES ('opt-1', ?, 'c1', 'GIS260401C00045000', 'sell', 'limit', 'filled',
                  '1', '1', '0.55', '0.55', 'us_option',
                  '2026-10-10T15:00:00+00:00', '2026-10-10T15:01:00+00:00', '{}', ?)
        """,
        (account_id, now),
    )
    # Equity sell for realized P&L
    conn.execute(
        """
        INSERT INTO orders (
            broker_order_id, account_id, client_order_id, symbol, side, order_type,
            status, qty, filled_qty, limit_price, filled_avg_price, asset_class,
            submitted_at, updated_at, raw_json, synced_at
        ) VALUES ('eq-1', ?, 'c2', 'GIS', 'sell', 'limit', 'filled',
                  '50', '50', '42', '42', 'us_equity',
                  '2026-10-20T15:00:00+00:00', '2026-10-20T15:01:00+00:00', '{}', ?)
        """,
        (account_id, now),
    )


def test_performance_nonzero_when_data_exists(tmp_path: Path) -> None:
    conn = open_migrated(tmp_path / "p.sqlite3")
    _seed_account(conn)
    perf = compute_performance(conn, account_id="paper-1", start="2026-10-01", end="2026-11-01")
    assert perf.dividends == Decimal("55.25")
    assert perf.net_option_premiums == Decimal("55")  # 1 * 0.55 * 100
    assert perf.unrealized_stock_pnl == Decimal("500")  # 4500 - 4000
    assert perf.realized_stock_pnl == Decimal("100")  # (42-40)*50
    report = build_monthly_report(conn, account_id="paper-1", period_key="2026-10")
    assert Decimal(report.dividends_received) == Decimal("55.25")
    assert Decimal(report.net_option_premiums) == Decimal("55")
    assert Decimal(report.realized_stock_pnl) == Decimal("100")
    assert Decimal(report.unrealized_stock_pnl) == Decimal("500")
    assert "placeholder" not in report.total_return_note
    conn.close()


def test_health_liveness_ok_operational_unhealthy_on_failed_job(tmp_path: Path) -> None:
    live = health_payload(AppConfig(), liveness_only=True)
    assert live["status"] == "ok"
    assert live["kind"] == "liveness"

    db = tmp_path / "h.sqlite3"
    conn = open_migrated(db)
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    conn.execute(
        """
        INSERT INTO job_runs (job_name, status, started_at, finished_at, detail, account_id)
        VALUES ('trading_day', 'failed', ?, ?, 'boom', NULL)
        """,
        (now, now),
    )
    conn.commit()
    conn.close()

    ops = health_payload(AppConfig(), db_path=db)
    assert ops["status"] == "unhealthy"
    assert ops["kind"] == "operational"
    assert ops["readiness_status"] == "not_ready"

    ready = readiness_payload(config=AppConfig(), db_path=db)
    assert ready["status"] == "not_ready"
    names = {c["name"]: c["status"] for c in ready["checks"]}
    assert names.get("recent_job_failures") == "fail"


def test_stale_job_freshness_fails(tmp_path: Path) -> None:
    db = tmp_path / "stale.sqlite3"
    conn = open_migrated(db)
    old = (datetime.now(UTC) - timedelta(hours=48)).replace(microsecond=0).isoformat()
    conn.execute(
        """
        INSERT INTO job_keys (
            job_name, period_key, status, result_json, created_at, updated_at
        ) VALUES ('startup', '2026-01-01:startup', 'completed', '{}', ?, ?)
        """,
        (old, old),
    )
    conn.commit()
    conn.close()
    report = readiness_payload(config=AppConfig(), db_path=db)
    assert report["status"] == "not_ready"
    freshness = next(c for c in report["checks"] if c["name"] == "job_freshness")
    assert freshness["status"] == "fail"
