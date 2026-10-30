"""Deterministic issuer screening hooks (yield / 52-week / liquidity placeholders)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from dividend_agent.config import StrategyConfig
from dividend_agent.research.citations import Citation, CriterionResult
from dividend_agent.research.dividends import DividendSnapshot


class MarketSnapshotInput(BaseModel):
    """Market fields that must come from broker/licensed feeds — never invented."""

    model_config = ConfigDict(frozen=True)

    price: Decimal | None = None
    price_as_of: date | None = None
    week52_high: Decimal | None = None
    week52_low: Decimal | None = None
    avg_dollar_volume: Decimal | None = None
    optionable: bool | None = None
    quote_citation: Citation | None = None


class ScreenResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    accepted: bool
    criteria: tuple[CriterionResult, ...] = ()
    reasons: tuple[str, ...] = ()
    review_flags: tuple[str, ...] = ()


def _yield_for_screen(div: DividendSnapshot) -> tuple[Decimal | None, str]:
    """Prefer trailing yield; fall back to labeled forward annualized-from-declared."""
    if div.trailing_yield is not None:
        return div.trailing_yield, "trailing"
    if div.forward_yield is not None:
        return div.forward_yield, "forecast_annualized_from_declared"
    return None, "missing"


def screen_issuer(
    ticker: str,
    *,
    strategy: StrategyConfig,
    dividends: DividendSnapshot,
    market: MarketSnapshotInput,
) -> ScreenResult:
    """Apply configurable gates; missing data => fail/review, never invent pass."""
    criteria: list[CriterionResult] = []
    reasons: list[str] = []
    review_flags: list[str] = []

    yld, yld_kind = _yield_for_screen(dividends)
    evidence = dividends.citations
    if yld is None:
        criteria.append(
            CriterionResult(
                name="dividend_yield_range",
                status="missing",
                detail="No yield available without declared dividends and market price",
                evidence=evidence,
            )
        )
        reasons.append("missing_dividend_yield")
    else:
        lo = Decimal(str(strategy.dividend_yield_target_min))
        hi = Decimal(str(strategy.dividend_yield_target_max))
        review_at = Decimal(str(strategy.unusually_high_yield_review_at))
        if yld > review_at:
            criteria.append(
                CriterionResult(
                    name="dividend_yield_range",
                    status="review",
                    detail=f"{yld_kind} yield {yld} above review threshold {review_at}",
                    evidence=evidence,
                )
            )
            review_flags.append("unusually_high_yield")
        elif lo <= yld <= hi:
            criteria.append(
                CriterionResult(
                    name="dividend_yield_range",
                    status="pass",
                    detail=f"{yld_kind} yield {yld} within [{lo}, {hi}]",
                    evidence=evidence,
                )
            )
        else:
            criteria.append(
                CriterionResult(
                    name="dividend_yield_range",
                    status="fail",
                    detail=f"{yld_kind} yield {yld} outside [{lo}, {hi}]",
                    evidence=evidence,
                )
            )
            reasons.append("yield_out_of_range")

    if strategy.inspect_52_week_range:
        if (
            market.price is None
            or market.week52_high is None
            or market.week52_low is None
            or market.week52_high <= market.week52_low
        ):
            criteria.append(
                CriterionResult(
                    name="52_week_range",
                    status="missing",
                    detail="52-week high/low/price missing — fail closed for this gate",
                    evidence=(market.quote_citation,) if market.quote_citation else (),
                )
            )
            reasons.append("missing_52_week_range")
        else:
            span = market.week52_high - market.week52_low
            position = (market.price - market.week52_low) / span
            # Soft gate: near the high (>90% of range) => review, not auto-pass.
            if position >= Decimal("0.90"):
                criteria.append(
                    CriterionResult(
                        name="52_week_range",
                        status="review",
                        detail=f"price near 52-week high (position={position:.2f})",
                        evidence=(market.quote_citation,) if market.quote_citation else (),
                        extras={"position_in_range": str(position)},
                    )
                )
                review_flags.append("near_52_week_high")
            else:
                criteria.append(
                    CriterionResult(
                        name="52_week_range",
                        status="pass",
                        detail=f"52-week position={position:.2f}",
                        evidence=(market.quote_citation,) if market.quote_citation else (),
                        extras={"position_in_range": str(position)},
                    )
                )

    # Liquidity: without avg dollar volume from market data, mark missing (not invent).
    if market.avg_dollar_volume is None:
        criteria.append(
            CriterionResult(
                name="liquidity",
                status="missing",
                detail="avg dollar volume not provided by market feed",
                evidence=(market.quote_citation,) if market.quote_citation else (),
            )
        )
        review_flags.append("liquidity_data_missing")
    elif market.avg_dollar_volume <= 0:
        criteria.append(
            CriterionResult(
                name="liquidity",
                status="fail",
                detail="non-positive avg dollar volume",
            )
        )
        reasons.append("illiquid")
    else:
        criteria.append(
            CriterionResult(
                name="liquidity",
                status="pass",
                detail=f"avg_dollar_volume={market.avg_dollar_volume}",
                evidence=(market.quote_citation,) if market.quote_citation else (),
            )
        )

    if market.optionable is False:
        criteria.append(CriterionResult(name="optionable", status="fail", detail="not optionable"))
        reasons.append("not_optionable")
    elif market.optionable is None:
        criteria.append(
            CriterionResult(
                name="optionable",
                status="missing",
                detail="optionability unknown",
            )
        )
        review_flags.append("optionability_unknown")
    else:
        criteria.append(CriterionResult(name="optionable", status="pass", detail="optionable"))

    hard_fail = any(
        c.status in {"fail", "missing"}
        and c.name
        in {
            "dividend_yield_range",
            "52_week_range",
        }
        for c in criteria
    )
    # Missing liquidity/optionable => not auto-accepted.
    soft_missing = any(
        c.status == "missing" and c.name in {"liquidity", "optionable"} for c in criteria
    )
    accepted = (not hard_fail) and (not soft_missing) and (not reasons) and (not review_flags)
    if review_flags and not reasons:
        reasons.append("requires_review")

    return ScreenResult(
        ticker=ticker.upper(),
        accepted=accepted,
        criteria=tuple(criteria),
        reasons=tuple(reasons),
        review_flags=tuple(review_flags),
    )
