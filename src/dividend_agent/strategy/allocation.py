"""Allocation / concentration checks against resolved §21 numeric limits.

Semantics (Milestone A):
- **Option order notional** = contracts × deliverable × limit_price (premium
  received). Compared to ``max_portfolio_order_notional_usd`` when set.
- **Stock order notional** = qty × limit_price (or market placeholder). Compared
  to ``max_new_stock_order_notional_usd`` when set.
- **Issuer allocation** = (existing issuer equity market value + incremental
  stock notional) / portfolio equity ≤ ``max_position_pct``.
- **Cash reserve** = cash − stock buy notional (calls add cash) ≥
  ``min_cash_reserve_usd``. Covered-call sells do not consume cash reserve.
"""

from __future__ import annotations

from decimal import Decimal

from dividend_agent.brokers.types import AccountSnapshot, PositionSnapshot
from dividend_agent.config import AppConfig
from dividend_agent.domain.models import TradeIntent
from dividend_agent.options.gates import CandidateEvaluation
from dividend_agent.portfolio.lots import CoverageState, contracts_coverable


def _underlying_from_intent(intent: TradeIntent) -> str | None:
    # Local import avoids allocation ↔ execution.guardrails cycle via package __init__.
    from dividend_agent.execution.coverage import underlying_from_intent

    return underlying_from_intent(intent)


def _is_option_intent(intent: TradeIntent) -> bool:
    underlying = _underlying_from_intent(intent)
    if underlying is None:
        return False
    return intent.symbol.upper() != underlying.upper() or intent.strike is not None


def option_premium_notional(intent: TradeIntent) -> Decimal | None:
    """Premium notional for a covered-call sell (contracts × 100 × limit)."""
    if intent.limit_price is None:
        return None
    deliverable = Decimal(intent.deliverable_shares or 100)
    return intent.quantity * deliverable * intent.limit_price


def stock_order_notional(intent: TradeIntent) -> Decimal | None:
    if intent.limit_price is None:
        return None
    return intent.quantity * intent.limit_price


def issuer_equity_market_value(
    positions: list[PositionSnapshot],
    underlying: str,
) -> Decimal:
    total = Decimal("0")
    for pos in positions:
        if pos.symbol.upper() != underlying.upper():
            continue
        if (pos.asset_class or "us_equity").lower() not in {"us_equity", "equity", "stock"}:
            continue
        if pos.market_value is not None:
            total += abs(pos.market_value)
        else:
            total += abs(pos.qty) * pos.avg_entry_price
    return total


def evaluate_allocation_limits(
    *,
    config: AppConfig,
    intent: TradeIntent,
    account: AccountSnapshot,
    positions: list[PositionSnapshot],
) -> list[str]:
    """Return hard-block reason codes when resolved numeric limits are violated.

    Null §21 fields are handled by ``assert_guardrails_resolved_for_auto_submit``
    before submission; this function enforces values once they are set.
    """
    reasons: list[str] = []
    exec_cfg = config.execution
    underlying = _underlying_from_intent(intent)
    is_option = _is_option_intent(intent)

    if account.trading_blocked or account.account_blocked:
        reasons.append("account_restricted")

    if is_option:
        # Covered calls require options permission when broker reports a level.
        level = account.options_approved_level
        if level is not None and level < 2:
            reasons.append(f"insufficient_options_permission:{level}")

        premium = option_premium_notional(intent)
        max_n = exec_cfg.max_portfolio_order_notional_usd
        if max_n is not None and premium is not None and premium > Decimal(str(max_n)):
            reasons.append(f"order_notional_exceeded:{premium}>{max_n}")
        elif max_n is not None and premium is None:
            reasons.append("missing_limit_price_for_notional_check")
    else:
        notional = stock_order_notional(intent)
        max_stock = exec_cfg.max_new_stock_order_notional_usd
        if max_stock is not None and notional is not None and notional > Decimal(str(max_stock)):
            reasons.append(f"stock_order_notional_exceeded:{notional}>{max_stock}")
        elif max_stock is not None and notional is None:
            reasons.append("missing_limit_price_for_notional_check")

        max_pct = exec_cfg.max_position_pct
        if max_pct is not None and underlying is not None and account.equity > 0:
            existing = issuer_equity_market_value(positions, underlying)
            incremental = notional or Decimal("0")
            # Buys increase issuer exposure; sells reduce it (not enforced here).
            if intent.side.lower() in {"buy", "buy_to_cover"}:
                projected = (existing + incremental) / account.equity
                if projected > Decimal(str(max_pct)):
                    reasons.append(f"issuer_allocation_exceeded:{projected}>{max_pct}")

        min_cash = exec_cfg.min_cash_reserve_usd
        if min_cash is not None and intent.side.lower() == "buy":
            spend = notional or Decimal("0")
            remaining = account.cash - spend
            if remaining < Decimal(str(min_cash)):
                reasons.append(f"cash_reserve_breach:{remaining}<{min_cash}")

    # Covered-call path: still enforce issuer concentration on underlying equity
    # already held (calls should not push issuer % via stock, but oversized
    # portfolios are blocked if max_position_pct is set and holdings exceed it).
    max_pct = exec_cfg.max_position_pct
    if is_option and max_pct is not None and underlying is not None and account.equity > 0:
        existing = issuer_equity_market_value(positions, underlying)
        pct = existing / account.equity
        if pct > Decimal(str(max_pct)):
            reasons.append(f"issuer_allocation_exceeded:{pct}>{max_pct}")

    # Cash reserve: covered-call sell does not spend cash; stock buys checked above.
    # Portfolio-level max order notional for options already applied.

    return reasons


# --- Milestone C: rank + allocate covered calls without overcommitting shares ---


def rank_covered_call_candidates(
    evaluations: list[CandidateEvaluation] | tuple[CandidateEvaluation, ...],
) -> list[CandidateEvaluation]:
    """Rank accepted candidates: higher COP, then higher premium, then farther OTM."""
    accepted = [e for e in evaluations if e.accepted]

    def key(e: CandidateEvaluation) -> tuple[Decimal, Decimal, Decimal]:
        cop = e.probability.chance_of_profit if e.probability is not None else Decimal("0")
        prem = e.premium_per_share or Decimal("0")
        # Farther OTM preferred when COP/premium tie — use strike as proxy with spot in extras
        strike = e.strike
        return (cop, prem, strike)

    return sorted(accepted, key=key, reverse=True)


def allocate_covered_calls(
    *,
    coverage: CoverageState,
    evaluations: list[CandidateEvaluation] | tuple[CandidateEvaluation, ...],
    contracts_per_lot: int = 1,
    deliverable: int = 100,
) -> tuple[list[CandidateEvaluation], list[CandidateEvaluation], list[str]]:
    """Select executable calls without overcommitting unencumbered shares.

    Returns ``(executable, alternatives, notes)``. Alternatives are accepted
    candidates that were not allocated due to coverage limits.
    """
    ranked = rank_covered_call_candidates(evaluations)
    remaining = coverage.unencumbered_shares
    executable: list[CandidateEvaluation] = []
    alternatives: list[CandidateEvaluation] = []
    notes: list[str] = []
    needed_per = Decimal(deliverable) * Decimal(contracts_per_lot)
    max_contracts = contracts_coverable(remaining, deliverable=deliverable)
    if max_contracts <= 0:
        notes.append("no_unencumbered_shares_for_allocation")
        return [], ranked, notes

    slots = max_contracts // max(1, contracts_per_lot)
    for candidate in ranked:
        if slots <= 0:
            alternatives.append(candidate)
            continue
        if remaining < needed_per:
            alternatives.append(candidate)
            continue
        executable.append(candidate)
        remaining -= needed_per
        slots -= 1

    if alternatives:
        notes.append(
            f"coverage_capped_executable={len(executable)}_alternatives={len(alternatives)}"
        )
    return executable, alternatives, notes
