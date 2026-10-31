"""Stock accumulation sizing toward a standard covered-call lot."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from dividend_agent.domain.models import TradeIntent
from dividend_agent.portfolio.lots import CoverageState


def shares_needed_for_lot(
    coverage: CoverageState | None,
    *,
    target_shares: int = 100,
) -> Decimal:
    """How many shares to buy to reach ``target_shares`` owned (not unencumbered)."""
    owned = coverage.shares_owned if coverage is not None else Decimal("0")
    need = Decimal(target_shares) - owned
    return need if need > 0 else Decimal("0")


def build_stock_buy_intent(
    *,
    account_id: str,
    symbol: str,
    quantity: Decimal,
    limit_price: Decimal,
    evidence_refs: tuple[str, ...] = (),
    rule_config_version: str = "strategy.schema.v1",
    extras: dict[str, str | int | float | bool] | None = None,
) -> TradeIntent:
    """Explainable equity buy intent (no option fields)."""
    if quantity <= 0:
        raise ValueError("stock buy quantity must be positive")
    payload: dict[str, str] = {
        "underlying": symbol.upper(),
        "intent_kind": "stock_buy",
        "submission": "orchestrated_m3d",
    }
    if extras:
        for key, value in extras.items():
            payload[key] = str(value)
    return TradeIntent(
        account_id=account_id,
        symbol=symbol.upper(),
        side="buy",
        quantity=quantity,
        order_type="limit",
        limit_price=limit_price,
        market_snapshot_at=datetime.now(UTC),
        evidence_refs=evidence_refs,
        rule_config_version=rule_config_version,
        extras=payload,
    )
