"""WAC and coverage / double-encumbrance tests."""

from __future__ import annotations

from decimal import Decimal

from dividend_agent.portfolio.lots import (
    CoverageState,
    ShareLot,
    contracts_coverable,
    weighted_average_cost,
)


def test_wac_multiple_lots() -> None:
    lots = (
        ShareLot(qty=Decimal("100"), cost_per_share=Decimal("40")),
        ShareLot(qty=Decimal("50"), cost_per_share=Decimal("46")),
    )
    # (4000 + 2300) / 150 = 42
    assert weighted_average_cost(lots) == Decimal("42")


def test_wac_fractional() -> None:
    lots = (ShareLot(qty=Decimal("33.5"), cost_per_share=Decimal("10.25")),)
    assert weighted_average_cost(lots) == Decimal("10.25")


def test_double_encumbrance_blocks_second_contract() -> None:
    state = CoverageState(
        symbol="GIS",
        lots=(ShareLot(qty=Decimal("100"), cost_per_share=Decimal("40")),),
        shares_reserved_by_open_calls=Decimal("100"),
    )
    assert state.unencumbered_shares == Decimal("0")
    assert contracts_coverable(state.unencumbered_shares) == 0


def test_one_hundred_shares_one_contract() -> None:
    assert contracts_coverable(Decimal("100")) == 1
    assert contracts_coverable(Decimal("199")) == 1
    assert contracts_coverable(Decimal("200")) == 2
