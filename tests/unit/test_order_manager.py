"""Paper order manager: idempotency, timeout, restart, hard blocks."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from dividend_agent.brokers.credentials import assert_paper_base_url
from dividend_agent.brokers.fake_paper import FakePaperBroker
from dividend_agent.brokers.interface import BrokerConfigError
from dividend_agent.config import AppConfig
from dividend_agent.domain.models import TradeIntent
from dividend_agent.execution.approval_queue import ApprovalError, ApprovalQueue
from dividend_agent.execution.guardrails import NullGuardrailError
from dividend_agent.execution.kill_switch import TradingPausedError
from dividend_agent.execution.order_manager import OrderManager, make_auto_config
from dividend_agent.execution.states import OrderState
from dividend_agent.portfolio.lots import CoverageState, ShareLot
from dividend_agent.storage import ExecutionRepository, open_migrated


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
    )
    base.update(kwargs)
    return TradeIntent(**base)  # type: ignore[arg-type]


def _mgr(tmp_path: Path, broker: FakePaperBroker, cfg: AppConfig | None = None) -> OrderManager:
    conn = open_migrated(tmp_path / "exec.sqlite3")
    # Keep connection open for test duration via broker attribute
    repo = ExecutionRepository(conn)
    mgr = OrderManager(
        config=cfg or make_auto_config(AppConfig()),
        broker=broker,
        repo=repo,
        environ={"TRADING_PAUSED": "false"},
    )
    mgr._conn = conn  # type: ignore[attr-defined]
    return mgr


def test_dry_run_does_not_submit(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    mgr = _mgr(tmp_path, broker, cfg=AppConfig())  # paper_dry_run
    row = mgr.process_intent(_intent())
    assert row["state"] == OrderState.READY.value
    assert broker.submit_count == 0


def test_null_guardrails_block_auto_submit(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    cfg = AppConfig().model_copy(
        update={
            "mode": "paper_auto",
            "execution": AppConfig().execution.model_copy(
                update={"auto_paper_orders_enabled": True}
            ),
        }
    )
    mgr = _mgr(tmp_path, broker, cfg=cfg)
    with pytest.raises(NullGuardrailError):
        mgr.process_intent(_intent())
    assert broker.submit_count == 0


def test_timeout_then_lookup_no_duplicate(tmp_path: Path) -> None:
    broker = FakePaperBroker(timeout_after_accept_once=True)
    mgr = _mgr(tmp_path, broker)
    intent = _intent()
    row = mgr.process_intent(intent)
    assert row["state"] == OrderState.ACKNOWLEDGED.value
    assert broker.submit_count == 1
    # Re-process same intent must not create a second broker order
    row2 = mgr.process_intent(intent)
    assert row2["broker_order_id"] == row["broker_order_id"]
    assert broker.submit_count == 1


def test_restart_recovery_mid_order(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    mgr = _mgr(tmp_path, broker)
    intent = _intent()
    row = mgr.process_intent(intent)
    client_id = row["client_order_id"]
    # Simulate crash after SUBMITTED recorded but before ack applied
    mgr._repo.upsert_local_order({**row, "state": OrderState.RECONCILIATION_NEEDED.value})
    recovered = mgr.recover_after_restart(client_id)
    assert recovered["broker_order_id"]
    assert recovered["state"] == OrderState.ACKNOWLEDGED.value
    assert broker.submit_count == 1


def test_hard_block_cannot_approve(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    mgr = _mgr(tmp_path, broker)
    coverage = CoverageState(
        symbol="GIS",
        lots=(ShareLot(qty=Decimal("100"), cost_per_share=Decimal("50")),),
    )
    intent = _intent(strike=Decimal("45"))  # below WAC 50
    row = mgr.process_intent(intent, coverage=coverage)
    assert row["state"] == OrderState.REJECTED.value
    assert row["hard_block"] == 1
    with pytest.raises(ApprovalError, match="hard blocks"):
        ApprovalQueue(mgr._repo).enqueue(
            client_order_id=row["client_order_id"],
            approval_type="exception",
            reason="try hard block",
            hard_block_reasons=["strike_below_wac"],
        )


def test_approval_expiry_and_supersede(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    broker = FakePaperBroker()
    mgr = _mgr(tmp_path, broker)
    intent = _intent()
    row = mgr.process_intent(intent, exception_reasons=["aggressive_cop_requires_approval"])
    assert row["state"] == OrderState.AWAITING_APPROVAL.value
    queue = ApprovalQueue(mgr._repo)
    first = mgr._repo.pending_approval_for_order(row["client_order_id"])
    assert first is not None
    # Supersede
    second_id = queue.enqueue(
        client_order_id=row["client_order_id"],
        approval_type="exception",
        reason="updated",
    )
    refreshed = mgr._repo.get_approval(first["approval_id"])
    assert refreshed is not None
    assert refreshed["status"] == "superseded"
    # Expire
    mgr._repo.update_approval(
        second_id,
        expires_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    )
    assert queue.expire_stale() >= 1
    expired = mgr._repo.get_approval(second_id)
    assert expired is not None
    assert expired["status"] == "expired"


def test_trading_paused(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    cfg = make_auto_config(AppConfig())
    conn = open_migrated(tmp_path / "p.sqlite3")
    mgr = OrderManager(
        config=cfg,
        broker=broker,
        repo=ExecutionRepository(conn),
        environ={"TRADING_PAUSED": "true"},
    )
    intent = _intent()
    with pytest.raises(TradingPausedError):
        mgr.process_intent(intent)
    assert broker.submit_count == 0


def test_live_endpoint_rejected() -> None:
    with pytest.raises(BrokerConfigError, match="live"):
        assert_paper_base_url("https://api.alpaca.markets")


def test_live_intent_hard_block(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    mgr = _mgr(tmp_path, broker)
    row = mgr.process_intent(_intent(extras={"live": True}))
    assert row["state"] == OrderState.REJECTED.value
    assert "live_destination" in row["rejection_reasons_json"]
