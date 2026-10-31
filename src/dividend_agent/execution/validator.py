"""Hard-gate revalidation for TradeIntents (pre-submit; Milestone A + D buys)."""

from __future__ import annotations

from decimal import Decimal

from dividend_agent.domain.models import TradeIntent
from dividend_agent.execution.coverage import underlying_from_intent
from dividend_agent.portfolio.lots import CoverageState

_CALL_SIDES = frozenset({"sell", "sell_to_open"})
_BUY_SIDES = frozenset({"buy", "buy_to_cover"})
_SUPPORTED_ORDER_TYPES = frozenset({"limit", "market"})


def _is_stock_buy(intent: TradeIntent) -> bool:
    if intent.side.lower() not in _BUY_SIDES:
        return False
    if intent.strike is not None:
        return False
    extras = intent.extras or {}
    if extras.get("intent_kind") == "stock_buy":
        return True
    underlying = underlying_from_intent(intent)
    return underlying is not None and intent.symbol.upper() == underlying.upper()


def validate_intent_coverage(
    intent: TradeIntent,
    coverage: CoverageState,
    *,
    expected_account_id: str | None = None,
) -> list[str]:
    """Ensure intent is covered (calls) or well-formed (stock buys).

    Absence of coverage/WAC is rejection for covered calls, never permission.
    Premium must not offset the strike floor. Stock buys skip share/WAC gates.
    """
    reasons: list[str] = []

    if intent.quantity is None or intent.quantity <= 0:
        reasons.append("invalid_quantity")

    if intent.order_type.lower() not in _SUPPORTED_ORDER_TYPES:
        reasons.append(f"unsupported_order_type:{intent.order_type}")

    if expected_account_id is not None and intent.account_id != expected_account_id:
        reasons.append(f"account_mismatch:{intent.account_id}!={expected_account_id}")

    underlying = underlying_from_intent(intent)
    if underlying is None:
        reasons.append("missing_underlying")
    elif coverage.symbol.upper() != underlying.upper():
        reasons.append(f"underlying_mismatch:{coverage.symbol}!={underlying}")

    if _is_stock_buy(intent):
        if intent.limit_price is None and intent.order_type.lower() == "limit":
            reasons.append("missing_limit_price_for_notional_check")
        return reasons

    if intent.side.lower() not in _CALL_SIDES:
        reasons.append("unsupported_side_for_covered_call")

    deliverable = intent.deliverable_shares
    if deliverable is None:
        reasons.append("missing_deliverable_shares")
        deliverable = 0
    elif deliverable != 100:
        reasons.append(f"nonstandard_deliverable_unsupported:{deliverable}")

    needed = Decimal(deliverable) * intent.quantity if deliverable else Decimal("0")
    if deliverable and coverage.unencumbered_shares < needed:
        reasons.append(f"insufficient_unencumbered_shares:{coverage.unencumbered_shares}<{needed}")

    wac = coverage.weighted_average_cost
    if wac is None:
        reasons.append("missing_wac")
    elif intent.strike is None:
        reasons.append("missing_strike")
    elif intent.strike < wac:
        # Strict Decimal compare; premium must not offset the floor.
        reasons.append(f"strike_below_wac:{intent.strike}<{wac}")

    return reasons
