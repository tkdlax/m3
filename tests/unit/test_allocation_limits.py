"""Numeric §21 limit enforcement (boundary + over-limit)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from dividend_agent.brokers.types import AccountSnapshot, PositionSnapshot
from dividend_agent.config import AppConfig
from dividend_agent.domain.models import TradeIntent
from dividend_agent.execution.guardrails import numeric_limit_violations
from dividend_agent.execution.order_manager import make_auto_config
from dividend_agent.strategy.allocation import evaluate_allocation_limits


def _account(*, cash: str = "10000", equity: str = "100000") -> AccountSnapshot:
    return AccountSnapshot(
        account_id="acct",
        status="ACTIVE",
        cash=Decimal(cash),
        buying_power=Decimal(cash),
        equity=Decimal(equity),
        portfolio_value=Decimal(equity),
        options_approved_level=3,
    )


def _call_intent(*, qty: str = "1", limit: str = "0.55") -> TradeIntent:
    return TradeIntent(
        account_id="acct",
        symbol="GIS260401C00045000",
        side="sell",
        quantity=Decimal(qty),
        order_type="limit",
        limit_price=Decimal(limit),
        strike=Decimal("45"),
        deliverable_shares=100,
        rule_config_version="v1",
        intent_id=uuid4(),
        extras={"underlying": "GIS"},
    )


def _stock_buy(*, qty: str = "10", limit: str = "40") -> TradeIntent:
    return TradeIntent(
        account_id="acct",
        symbol="GIS",
        side="buy",
        quantity=Decimal(qty),
        order_type="limit",
        limit_price=Decimal(limit),
        rule_config_version="v1",
        intent_id=uuid4(),
        extras={"underlying": "GIS"},
    )


def test_option_notional_boundary_and_over() -> None:
    cfg = make_auto_config(AppConfig())
    # max_portfolio_order_notional_usd = 5000; 1*100*0.55 = 55 OK
    ok = evaluate_allocation_limits(
        config=cfg, intent=_call_intent(), account=_account(), positions=[]
    )
    assert ok == []
    # 100 contracts * 100 * 0.55 = 5500 > 5000
    bad = evaluate_allocation_limits(
        config=cfg,
        intent=_call_intent(qty="100"),
        account=_account(),
        positions=[],
    )
    assert any(r.startswith("order_notional_exceeded") for r in bad)


def test_stock_notional_and_cash_reserve() -> None:
    cfg = make_auto_config(AppConfig())
    # max_new_stock_order_notional_usd=2500; 10*40=400 OK; cash 10000-400 >= 1000
    ok = evaluate_allocation_limits(
        config=cfg, intent=_stock_buy(), account=_account(), positions=[]
    )
    assert ok == []
    over = evaluate_allocation_limits(
        config=cfg,
        intent=_stock_buy(qty="100", limit="40"),  # 4000 > 2500
        account=_account(),
        positions=[],
    )
    assert any(r.startswith("stock_order_notional_exceeded") for r in over)

    cash_breach = evaluate_allocation_limits(
        config=cfg,
        intent=_stock_buy(qty="50", limit="40"),  # notional 2000 OK vs 2500; cash 1500-2000
        account=_account(cash="1500"),
        positions=[],
    )
    # 50*40=2000 <= 2500, cash remaining -500 < 1000
    assert any(r.startswith("cash_reserve_breach") for r in cash_breach)


def test_issuer_allocation_over_limit() -> None:
    cfg = make_auto_config(AppConfig())  # max_position_pct=0.1
    positions = [
        PositionSnapshot(
            symbol="GIS",
            qty=Decimal("1000"),
            avg_entry_price=Decimal("50"),
            market_value=Decimal("50000"),
            side="long",
        )
    ]
    # 50000/100000 = 0.5 > 0.1
    bad = numeric_limit_violations(
        config=cfg,
        intent=_call_intent(),
        account=_account(),
        positions=positions,
    )
    assert any(r.startswith("issuer_allocation_exceeded") for r in bad)
