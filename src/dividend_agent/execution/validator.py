"""Hard-gate revalidation for TradeIntents (pre-submit; M3 explainability)."""

from __future__ import annotations

from decimal import Decimal

from dividend_agent.domain.models import TradeIntent
from dividend_agent.portfolio.lots import CoverageState


def validate_intent_coverage(
    intent: TradeIntent,
    coverage: CoverageState,
) -> list[str]:
    """Ensure intent does not double-encumber shares. No submission."""
    reasons: list[str] = []
    deliverable = intent.deliverable_shares or 100
    needed = Decimal(deliverable) * intent.quantity
    if coverage.unencumbered_shares < needed:
        reasons.append(f"insufficient_unencumbered_shares:{coverage.unencumbered_shares}<{needed}")
    wac = coverage.weighted_average_cost
    if intent.strike is not None and wac is not None and intent.strike < wac:
        reasons.append(f"strike_below_wac:{intent.strike}<{wac}")
    if intent.side.lower() not in {"sell", "sell_to_open"}:
        reasons.append("unsupported_side_for_covered_call")
    return reasons
