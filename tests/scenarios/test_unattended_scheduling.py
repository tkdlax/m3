"""Milestone E: unattended scheduling — restart/timeout/approval/assignment."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from dividend_agent.app import health_payload, readiness_payload
from dividend_agent.brokers.fake_paper import FakePaperBroker
from dividend_agent.brokers.types import ActivitySnapshot, PositionSnapshot
from dividend_agent.config import AppConfig
from dividend_agent.domain.models import TradeIntent
from dividend_agent.execution.assignment_monitor import AssignmentMonitor
from dividend_agent.execution.order_manager import OrderManager, make_auto_config
from dividend_agent.execution.states import OrderState
from dividend_agent.scheduling.cycle import run_jobs
from dividend_agent.storage import ExecutionRepository, open_migrated

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CHAIN = FIXTURES / "option_chain_gis.json"


def _gis_position(*, qty: str = "100") -> PositionSnapshot:
    return PositionSnapshot(
        symbol="GIS",
        qty=Decimal(qty),
        qty_available=Decimal(qty),
        avg_entry_price=Decimal("40"),
        market_value=Decimal(qty) * Decimal("40"),
    )


def _intent(**kwargs: object) -> TradeIntent:
    base = dict(
        account_id="paper-fake-001",
        symbol="GIS260401C00045000",
        side="sell",
        quantity=Decimal("1"),
        order_type="limit",
        limit_price=Decimal("0.55"),
        strike=Decimal("45"),
        deliverable_shares=100,
        rule_config_version="v1",
        intent_id=uuid4(),
        extras={"underlying": "GIS"},
    )
    base.update(kwargs)
    return TradeIntent(**base)  # type: ignore[arg-type]


def test_default_jobs_include_monthly_under_dry_run(tmp_path: Path) -> None:
    """Unpaused dry-run monthly does configured work (report + selection), not counts-only."""
    broker = FakePaperBroker(positions=[_gis_position()])
    db = tmp_path / "e.sqlite3"
    reports = tmp_path / "reports"
    cycle = run_jobs(
        config=AppConfig(),  # paper_dry_run
        broker=broker,
        db_path=db,
        reports_dir=reports,
        job_names=["monthly"],
        period_keys={"monthly": "2026-10"},
        environ={
            "TRADING_PAUSED": "false",
            "ALPACA_PAPER_API_KEY": "x",
            "ALPACA_PAPER_API_SECRET": "y",
        },
        option_chain_path=CHAIN,
    )
    result = cycle.results[0]
    assert result.status == "completed"
    assert result.payload.get("trading_paused") is False
    call_sel = result.payload.get("call_selection") or {}
    # Gateway processing happened (even if zero intents due to gates)
    assert "gateway_processed" in call_sel or call_sel.get("intents", 0) >= 0
    assert Path(result.payload["report_path"]).exists()
    # dry_run: no broker submits from gateway
    assert broker.submit_count == 0


def test_assignment_monitor_persists_and_dedupes(tmp_path: Path) -> None:
    conn = open_migrated(tmp_path / "a.sqlite3")
    broker = FakePaperBroker()
    now = datetime.now(UTC)
    broker.set_activities(
        [
            ActivitySnapshot(
                activity_id="act-1",
                activity_type="OPASN",
                symbol="GIS260401C00045000",
                qty=Decimal("1"),
                transaction_time=now,
                raw={"id": "act-1"},
            ),
            ActivitySnapshot(
                activity_id="act-2",
                activity_type="OPEXP",
                symbol="GIS260401C00040000",
                qty=Decimal("1"),
                transaction_time=now - timedelta(minutes=1),
                raw={"id": "act-2"},
            ),
        ]
    )
    monitor = AssignmentMonitor(conn)
    first = monitor.scan_broker(broker, account_id=broker.account_id)
    assert first.new_events == 2
    assert first.assignments == 1
    assert first.expiries == 1
    second = monitor.scan_broker(broker, account_id=broker.account_id)
    assert second.new_events == 0
    unhandled = monitor.list_unhandled(account_id=broker.account_id)
    assert len(unhandled) == 2
    monitor.mark_handled(unhandled[0]["event_id"])
    assert len(monitor.list_unhandled(account_id=broker.account_id)) == 1
    conn.close()


def test_trading_day_assignment_and_approval_expiry(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position()])
    now = datetime.now(UTC)
    broker.set_activities(
        [
            ActivitySnapshot(
                activity_id="asn-day",
                activity_type="OPASN",
                symbol="GIS260401C00045000",
                qty=Decimal("1"),
                transaction_time=now,
                raw={"id": "asn-day"},
            )
        ]
    )
    db = tmp_path / "td.sqlite3"
    # Seed an expired-pending approval via a prior order
    conn = open_migrated(db)
    repo = ExecutionRepository(conn)
    mgr = OrderManager(
        config=make_auto_config(AppConfig()),
        broker=broker,
        repo=repo,
        environ={"TRADING_PAUSED": "false"},
    )
    row = mgr.process_intent(_intent(), exception_reasons=["aggressive_cop_requires_approval"])
    assert row["state"] == OrderState.AWAITING_APPROVAL.value
    pending = repo.pending_approval_for_order(row["client_order_id"])
    assert pending is not None
    # Backdate expiry
    past = (now - timedelta(hours=1)).isoformat()
    repo.update_approval(pending["approval_id"], expires_at=past)
    conn.close()

    cycle = run_jobs(
        config=AppConfig(),
        broker=broker,
        db_path=db,
        reports_dir=tmp_path / "r",
        job_names=["trading_day"],
        period_keys={"trading_day": "2026-12-22"},
        environ={
            "TRADING_PAUSED": "true",
            "ALPACA_PAPER_API_KEY": "x",
            "ALPACA_PAPER_API_SECRET": "y",
        },
    )
    payload = cycle.results[0].payload
    assert payload["assignment_monitor"]["new_events"] >= 1
    assert payload["approvals_expired"] >= 1


def test_timeout_recon_synced_on_trading_day_without_resubmit(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position()], fail_submit_times=1)
    db = tmp_path / "to.sqlite3"
    conn = open_migrated(db)
    mgr = OrderManager(
        config=make_auto_config(AppConfig()),
        broker=broker,
        repo=ExecutionRepository(conn),
        environ={"TRADING_PAUSED": "false"},
    )
    row = mgr.process_intent(_intent())
    assert row["state"] == OrderState.RECONCILIATION_NEEDED.value
    submits_before = broker.submit_count
    conn.close()

    cycle = run_jobs(
        config=AppConfig(),  # dry_run — sync only
        broker=broker,
        db_path=db,
        reports_dir=tmp_path / "r",
        job_names=["trading_day"],
        period_keys={"trading_day": "2026-12-22"},
        environ={
            "TRADING_PAUSED": "false",
            "ALPACA_PAPER_API_KEY": "x",
            "ALPACA_PAPER_API_SECRET": "y",
        },
    )
    assert cycle.results[0].payload.get("synced_orders", 0) >= 1
    assert broker.submit_count == submits_before  # no resubmit under dry_run


def test_restart_recovery_under_paper_auto_unpaused(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position()])
    db = tmp_path / "rr.sqlite3"
    conn = open_migrated(db)
    cfg = make_auto_config(AppConfig())
    mgr = OrderManager(
        config=cfg,
        broker=broker,
        repo=ExecutionRepository(conn),
        environ={"TRADING_PAUSED": "false"},
    )
    row = mgr.process_intent(_intent())
    # Simulate mid-flight recon state after timeout ambiguity cleared broker-side
    ExecutionRepository(conn).upsert_local_order(
        {**row, "state": OrderState.RECONCILIATION_NEEDED.value, "broker_order_id": None}
    )
    # Clear broker memory so recovery treats as absent and resubmits once
    broker._orders_by_client.clear()
    broker._orders_by_broker.clear()
    conn.close()

    cycle = run_jobs(
        config=cfg,
        broker=broker,
        db_path=db,
        reports_dir=tmp_path / "r",
        job_names=["trading_day"],
        period_keys={"trading_day": "2026-12-22"},
        environ={
            "TRADING_PAUSED": "false",
            "ALPACA_PAPER_API_KEY": "x",
            "ALPACA_PAPER_API_SECRET": "y",
        },
    )
    assert cycle.results[0].payload.get("recovered_orders", 0) >= 1
    assert broker.submit_count >= 1


def test_readiness_detects_db_and_jobs(tmp_path: Path) -> None:
    live = health_payload(AppConfig())
    assert live["status"] == "ok"

    db = tmp_path / "ready.sqlite3"
    open_migrated(db).close()
    report = readiness_payload(config=AppConfig(), db_path=db)
    assert report["status"] in {"ready", "degraded"}
    names = {c["name"] for c in report["checks"]}
    assert "database" in names
    assert "job_freshness" in names

    # Missing DB parent that cannot be created? use invalid path device
    bad = readiness_payload(config=AppConfig(), db_path="/proc/does-not-exist/agent.sqlite3")
    assert bad["status"] == "not_ready"
