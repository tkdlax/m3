"""Paper order manager: idempotency, timeout, restart, hard blocks, coverage."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from dividend_agent.brokers.credentials import assert_paper_base_url
from dividend_agent.brokers.fake_paper import FakePaperBroker
from dividend_agent.brokers.interface import BrokerConfigError
from dividend_agent.brokers.types import PositionSnapshot
from dividend_agent.config import AppConfig
from dividend_agent.domain.models import TradeIntent
from dividend_agent.execution.approval_queue import ApprovalError, ApprovalQueue
from dividend_agent.execution.guardrails import NullGuardrailError
from dividend_agent.execution.kill_switch import InvalidPauseSettingError, TradingPausedError
from dividend_agent.execution.order_manager import OrderManager, make_auto_config
from dividend_agent.execution.states import OrderState
from dividend_agent.portfolio.lots import CoverageState, ShareLot
from dividend_agent.storage import ExecutionRepository, open_migrated


def _gis_position(*, qty: str = "100", cost: str = "40") -> PositionSnapshot:
    return PositionSnapshot(
        symbol="GIS",
        qty=Decimal(qty),
        qty_available=Decimal(qty),
        avg_entry_price=Decimal(cost),
        market_value=Decimal(qty) * Decimal(cost),
        side="long",
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


def _mgr(
    tmp_path: Path,
    broker: FakePaperBroker,
    cfg: AppConfig | None = None,
    *,
    name: str = "exec.sqlite3",
) -> OrderManager:
    conn = open_migrated(tmp_path / name)
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
    broker = FakePaperBroker(positions=[_gis_position()])
    mgr = _mgr(tmp_path, broker, cfg=AppConfig())  # paper_dry_run
    row = mgr.process_intent(_intent())
    assert row["state"] == OrderState.READY.value
    assert broker.submit_count == 0


def test_null_guardrails_block_auto_submit(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position()])
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


def test_missing_coverage_huge_qty_zero_submits(tmp_path: Path) -> None:
    """Handoff repro: huge qty + no holdings/coverage must not reach the broker."""
    broker = FakePaperBroker()  # empty positions → derived coverage has no WAC
    mgr = _mgr(tmp_path, broker)
    intent = _intent(quantity=Decimal("999999"), strike=Decimal("1"))
    row = mgr.process_intent(intent)  # no caller coverage; broker has nothing
    assert row["state"] == OrderState.REJECTED.value
    assert row["hard_block"] == 1
    reasons = row["rejection_reasons_json"]
    assert "missing_wac" in reasons or "insufficient_unencumbered" in reasons
    assert broker.submit_count == 0


def test_unresolvable_underlying_missing_coverage(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    mgr = _mgr(tmp_path, broker)
    intent = _intent(symbol="NOT_AN_OCC", extras={})
    row = mgr.process_intent(intent)
    assert row["state"] == OrderState.REJECTED.value
    assert "missing_coverage" in row["rejection_reasons_json"]
    assert broker.submit_count == 0


def test_insufficient_shares_99_for_one_call(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position(qty="99")])
    mgr = _mgr(tmp_path, broker)
    row = mgr.process_intent(_intent())
    assert row["state"] == OrderState.REJECTED.value
    assert "insufficient_unencumbered_shares" in row["rejection_reasons_json"]
    assert broker.submit_count == 0


def test_strike_below_wac_blocked(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position(cost="50")])
    mgr = _mgr(tmp_path, broker)
    row = mgr.process_intent(_intent(strike=Decimal("45")))
    assert row["state"] == OrderState.REJECTED.value
    assert "strike_below_wac" in row["rejection_reasons_json"]
    assert broker.submit_count == 0


def test_competing_reservations_second_rejected(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position(qty="100")])
    mgr = _mgr(tmp_path, broker, cfg=AppConfig())  # dry-run READY + reserve
    first = mgr.process_intent(_intent(intent_id=uuid4(), limit_price=Decimal("0.50")))
    assert first["state"] == OrderState.READY.value
    second = mgr.process_intent(
        _intent(intent_id=uuid4(), limit_price=Decimal("0.60"), strike=Decimal("46"))
    )
    assert second["state"] == OrderState.REJECTED.value
    assert broker.submit_count == 0


def test_account_mismatch_hard_block(tmp_path: Path) -> None:
    broker = FakePaperBroker(account_id="other-account", positions=[_gis_position()])
    mgr = _mgr(tmp_path, broker)
    row = mgr.process_intent(_intent(account_id="paper-fake-001"))
    assert row["state"] == OrderState.REJECTED.value
    assert "account_mismatch" in row["rejection_reasons_json"]
    assert broker.submit_count == 0


def test_timeout_then_lookup_no_duplicate(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position()], timeout_after_accept_once=True)
    mgr = _mgr(tmp_path, broker)
    intent = _intent()
    row = mgr.process_intent(intent)
    assert row["state"] == OrderState.ACKNOWLEDGED.value
    assert broker.submit_count == 1
    row2 = mgr.process_intent(intent)
    assert row2["broker_order_id"] == row["broker_order_id"]
    assert broker.submit_count == 1


def test_timeout_ambiguity_does_not_resubmit(tmp_path: Path) -> None:
    """After accept+timeout, empty confirmed lookup stays recon — no second submit."""
    broker = FakePaperBroker(positions=[_gis_position()], fail_submit_times=1)
    mgr = _mgr(tmp_path, broker)
    intent = _intent()
    # First submit raises before accept (fail_submit_times) — order never stored.
    # With fail_submit_times=1, first attempt fails without storing; OrderManager
    # now leaves RECONCILIATION_NEEDED without retrying.
    row = mgr.process_intent(intent)
    assert row["state"] == OrderState.RECONCILIATION_NEEDED.value
    assert broker.submit_count == 0


def test_lookup_failure_preserves_uncertainty(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position()], fail_lookup=True)
    mgr = _mgr(tmp_path, broker)
    # Seed a READY order then force submit path with lookup failure before submit.
    intent = _intent()
    row = mgr.process_intent(intent, force_dry_run=True)
    assert row["state"] == OrderState.READY.value
    # Enable auto and submit
    mgr._config = make_auto_config(AppConfig())
    broker._fail_lookup = True
    out = mgr.submit_ready_order(row["client_order_id"])
    assert out["state"] == OrderState.RECONCILIATION_NEEDED.value
    assert broker.submit_count == 0


def test_restart_recovery_mid_order(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position()])
    mgr = _mgr(tmp_path, broker)
    intent = _intent()
    row = mgr.process_intent(intent)
    client_id = row["client_order_id"]
    mgr._repo.upsert_local_order({**row, "state": OrderState.RECONCILIATION_NEEDED.value})
    recovered = mgr.recover_after_restart(client_id)
    assert recovered["broker_order_id"]
    assert recovered["state"] == OrderState.ACKNOWLEDGED.value
    assert broker.submit_count == 1


def test_holdings_changed_after_approval_blocks_submit(tmp_path: Path) -> None:
    from dividend_agent.execution.order_manager import OrderManagerError

    broker = FakePaperBroker(positions=[_gis_position()])
    mgr = _mgr(tmp_path, broker)
    intent = _intent()
    row = mgr.process_intent(intent, exception_reasons=["aggressive_cop_requires_approval"])
    assert row["state"] == OrderState.AWAITING_APPROVAL.value
    approval = mgr._repo.pending_approval_for_order(row["client_order_id"])
    assert approval is not None
    # Holdings disappear before approval completes
    broker.set_positions([])
    with pytest.raises(OrderManagerError):
        mgr.apply_approval_and_submit(approval["approval_id"])
    assert broker.submit_count == 0


def test_hard_block_cannot_approve(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position(cost="50")])
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

    broker = FakePaperBroker(positions=[_gis_position()])
    mgr = _mgr(tmp_path, broker)
    intent = _intent()
    row = mgr.process_intent(intent, exception_reasons=["aggressive_cop_requires_approval"])
    assert row["state"] == OrderState.AWAITING_APPROVAL.value
    queue = ApprovalQueue(mgr._repo)
    first = mgr._repo.pending_approval_for_order(row["client_order_id"])
    assert first is not None
    second_id = queue.enqueue(
        client_order_id=row["client_order_id"],
        approval_type="exception",
        reason="updated",
    )
    refreshed = mgr._repo.get_approval(first["approval_id"])
    assert refreshed is not None
    assert refreshed["status"] == "superseded"
    mgr._repo.update_approval(
        second_id,
        expires_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    )
    assert queue.expire_stale() >= 1
    expired = mgr._repo.get_approval(second_id)
    assert expired is not None
    assert expired["status"] == "expired"


def test_trading_paused(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position()])
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


def test_invalid_pause_typo_fail_closed(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position()])
    cfg = make_auto_config(AppConfig())
    conn = open_migrated(tmp_path / "typo.sqlite3")
    mgr = OrderManager(
        config=cfg,
        broker=broker,
        repo=ExecutionRepository(conn),
        environ={"TRADING_PAUSED": "typo"},
    )
    with pytest.raises((TradingPausedError, InvalidPauseSettingError)):
        mgr.process_intent(_intent())
    assert broker.submit_count == 0


def test_live_endpoint_rejected() -> None:
    with pytest.raises(BrokerConfigError, match="live"):
        assert_paper_base_url("https://api.alpaca.markets")


def test_live_intent_hard_block(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position()])
    mgr = _mgr(tmp_path, broker)
    row = mgr.process_intent(_intent(extras={"live": True, "underlying": "GIS"}))
    assert row["state"] == OrderState.REJECTED.value
    assert "live_destination" in row["rejection_reasons_json"]


def test_decision_identity_dedupes_regenerated_uuid(tmp_path: Path) -> None:
    broker = FakePaperBroker(positions=[_gis_position()])
    mgr = _mgr(tmp_path, broker)
    a = _intent(intent_id=uuid4())
    b = _intent(intent_id=uuid4())  # same logical decision, new UUID
    row1 = mgr.process_intent(a)
    row2 = mgr.process_intent(b)
    assert row1["client_order_id"] == row2["client_order_id"]
    assert broker.submit_count == 1
