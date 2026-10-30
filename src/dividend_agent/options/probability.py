"""Chance-of-profit and P(OTM) model (spec §9).

Risk-neutral Black–Scholes-style terminal probabilities using observed IV when
provided. This is **not** Robinhood Chance of Profit parity and does **not**
estimate American early-assignment probability.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProbabilityModelError(ValueError):
    """Missing inputs required for probability (fail closed)."""


def norm_cdf(x: float) -> float:
    """Standard normal CDF via ``math.erf`` (Abramowitz-quality for tests)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class BsInputs:
    spot: Decimal
    strike: Decimal
    premium_per_share: Decimal
    days_to_expiry: int
    implied_volatility: Decimal
    risk_free_rate: Decimal = Decimal("0.05")
    dividend_yield: Decimal = Decimal("0")  # continuous q; 0 if unknown (documented)


class ProbabilityResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    chance_of_profit: Decimal
    prob_expire_otm: Decimal
    breakeven: Decimal  # strike + premium_per_share
    premium_per_share: Decimal
    premium_per_contract: Decimal
    model: str = "bs_terminal_risk_neutral_v1"
    assumptions: tuple[str, ...] = ()
    inputs: dict[str, str] = Field(default_factory=dict)


def year_fraction(days: int) -> float:
    if days <= 0:
        raise ProbabilityModelError("days_to_expiry must be positive")
    return days / 365.0


def _d2(spot: float, barrier: float, t: float, r: float, q: float, sigma: float) -> float:
    if spot <= 0 or barrier <= 0 or sigma <= 0 or t <= 0:
        raise ProbabilityModelError("invalid BS inputs")
    return (math.log(spot / barrier) + (r - q - 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))


def terminal_prob_below(
    spot: Decimal,
    barrier: Decimal,
    *,
    days_to_expiry: int,
    implied_volatility: Decimal,
    risk_free_rate: Decimal = Decimal("0.05"),
    dividend_yield: Decimal = Decimal("0"),
) -> Decimal:
    """Risk-neutral P(S_T < barrier) = N(-d2)."""
    t = year_fraction(days_to_expiry)
    d2 = _d2(
        float(spot),
        float(barrier),
        t,
        float(risk_free_rate),
        float(dividend_yield),
        float(implied_volatility),
    )
    return Decimal(str(round(norm_cdf(-d2), 10)))


def compute_short_call_probabilities(inputs: BsInputs) -> ProbabilityResult:
    """COP = P(S_T < strike + premium); OTM = P(S_T < strike)."""
    if inputs.implied_volatility <= 0:
        raise ProbabilityModelError("implied_volatility required and must be > 0")
    if inputs.premium_per_share < 0:
        raise ProbabilityModelError("premium_per_share must be >= 0")
    if inputs.spot <= 0 or inputs.strike <= 0:
        raise ProbabilityModelError("spot and strike must be positive")

    breakeven = inputs.strike + inputs.premium_per_share
    assumptions_list = [
        "risk_neutral_lognormal_terminal",
        "european_style_approximation",
        "does_not_model_early_assignment",
        "not_robinhood_parity",
        "premium_is_per_share_bid_or_conservative_limit",
        "fees_ignored_in_cop",
    ]
    if inputs.dividend_yield == 0:
        assumptions_list.append("dividend_yield_assumed_zero_if_unknown")
    assumptions = tuple(assumptions_list)

    cop = terminal_prob_below(
        inputs.spot,
        breakeven,
        days_to_expiry=inputs.days_to_expiry,
        implied_volatility=inputs.implied_volatility,
        risk_free_rate=inputs.risk_free_rate,
        dividend_yield=inputs.dividend_yield,
    )
    otm = terminal_prob_below(
        inputs.spot,
        inputs.strike,
        days_to_expiry=inputs.days_to_expiry,
        implied_volatility=inputs.implied_volatility,
        risk_free_rate=inputs.risk_free_rate,
        dividend_yield=inputs.dividend_yield,
    )
    return ProbabilityResult(
        chance_of_profit=cop,
        prob_expire_otm=otm,
        breakeven=breakeven,
        premium_per_share=inputs.premium_per_share,
        premium_per_contract=inputs.premium_per_share * Decimal("100"),
        assumptions=assumptions,
        inputs={
            "spot": str(inputs.spot),
            "strike": str(inputs.strike),
            "premium_per_share": str(inputs.premium_per_share),
            "days_to_expiry": str(inputs.days_to_expiry),
            "iv": str(inputs.implied_volatility),
            "r": str(inputs.risk_free_rate),
            "q": str(inputs.dividend_yield),
        },
    )


def days_between(as_of: date, expiration: date) -> int:
    return (expiration - as_of).days
