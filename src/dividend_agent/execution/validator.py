"""Hard-gate revalidation for TradeIntents (pre-submit; Milestone A)."""

from __future__ import annotations

from decimal import Decimal

from dividend_agent.domain.models import TradeIntent
from dividend_agent.execution.coverage import underlying_from_intent
from dividend_agent.portfolio.lots import CoverageState

_SUPPORTED_SIDES = frozenset({"sell", "sell_to_open"})
_SUPPORTED_ORDER_TYPES = frozenset({"limit", "market"})


def validate_intent_coverage(
    intent: TradeIntent,
    coverage: CoverageState,
    *,
    expected_account_id: str | None = None,
) -> list[str]:
    """Ensure intent is covered and strike is not below unrounded WAC.

    Absence of coverage/WAC is rejection, never permission. Premium must not
    offset the strike floor.
    """
    reasons: list[str] = []

    if intent.quantity is None or intent.quantity <= 0:
        reasons.append("invalid_quantity")

    if intent.order_type.lower() not in _SUPPORTED_ORDER_TYPES:
        reasons.append(f"unsupported_order_type:{intent.order_type}")

    if intent.side.lower() not in _SUPPORTED_SIDES:
        reasons.append("unsupported_side_for_covered_call")

    underlying = underlying_from_intent(intent)
    if underlying is None:
        reasons.append("missing_underlying")
    elif coverage.symbol.upper() != underlying.upper():
        reasons.append(f"underlying_mismatch:{coverage.symbol}!={underlying}")

    if expected_account_id is not None and intent.account_id != expected_account_id:
        reasons.append(f"account_mismatch:{intent.account_id}!={expected_account_id}")

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
