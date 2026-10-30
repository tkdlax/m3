"""Probability model analytic / edge tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from dividend_agent.options.probability import (
    BsInputs,
    ProbabilityModelError,
    compute_short_call_probabilities,
    norm_cdf,
    terminal_prob_below,
)


def test_norm_cdf_known_points() -> None:
    assert abs(norm_cdf(0.0) - 0.5) < 1e-12
    assert norm_cdf(3.0) > 0.998
    assert norm_cdf(-3.0) < 0.002


def test_deep_otm_barrier_high_prob() -> None:
    # Spot far below barrier => high P(S_T < barrier)
    p = terminal_prob_below(
        Decimal("50"),
        Decimal("100"),
        days_to_expiry=30,
        implied_volatility=Decimal("0.20"),
        risk_free_rate=Decimal("0"),
        dividend_yield=Decimal("0"),
    )
    assert p > Decimal("0.95")


def test_deep_itm_barrier_low_prob() -> None:
    p = terminal_prob_below(
        Decimal("100"),
        Decimal("50"),
        days_to_expiry=30,
        implied_volatility=Decimal("0.20"),
        risk_free_rate=Decimal("0"),
        dividend_yield=Decimal("0"),
    )
    assert p < Decimal("0.05")


def test_cop_exceeds_otm_when_premium_positive() -> None:
    result = compute_short_call_probabilities(
        BsInputs(
            spot=Decimal("42"),
            strike=Decimal("45"),
            premium_per_share=Decimal("0.55"),
            days_to_expiry=31,
            implied_volatility=Decimal("0.22"),
            risk_free_rate=Decimal("0.05"),
            dividend_yield=Decimal("0"),
        )
    )
    assert result.chance_of_profit >= result.prob_expire_otm
    assert result.breakeven == Decimal("45.55")
    assert result.premium_per_contract == Decimal("55.00")
    assert "not_robinhood_parity" in result.assumptions


def test_missing_iv_fails() -> None:
    with pytest.raises(ProbabilityModelError):
        compute_short_call_probabilities(
            BsInputs(
                spot=Decimal("42"),
                strike=Decimal("45"),
                premium_per_share=Decimal("0.55"),
                days_to_expiry=30,
                implied_volatility=Decimal("0"),
            )
        )
