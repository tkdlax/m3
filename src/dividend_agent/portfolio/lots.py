"""Lot / cost-basis helpers (WAC) for covered-call strike floor."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator


class ShareLot(BaseModel):
    model_config = ConfigDict(frozen=True)

    qty: Decimal
    cost_per_share: Decimal

    @model_validator(mode="after")
    def _positive(self) -> ShareLot:
        if self.qty <= 0:
            raise ValueError("lot qty must be positive")
        if self.cost_per_share < 0:
            raise ValueError("cost_per_share must be >= 0")
        return self


class CoverageState(BaseModel):
    """Share availability for covered calls (no double encumbrance)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    lots: tuple[ShareLot, ...] = ()
    shares_reserved_by_open_calls: Decimal = Decimal("0")
    shares_reserved_by_pending_sells: Decimal = Decimal("0")

    @property
    def shares_owned(self) -> Decimal:
        return sum((lot.qty for lot in self.lots), Decimal("0"))

    @property
    def weighted_average_cost(self) -> Decimal | None:
        return weighted_average_cost(self.lots)

    @property
    def unencumbered_shares(self) -> Decimal:
        reserved = self.shares_reserved_by_open_calls + self.shares_reserved_by_pending_sells
        return self.shares_owned - reserved


def weighted_average_cost(lots: tuple[ShareLot, ...] | list[ShareLot]) -> Decimal | None:
    """Unrounded Decimal WAC across lots; None if empty."""
    total_qty = sum((lot.qty for lot in lots), Decimal("0"))
    if total_qty <= 0:
        return None
    total_cost = sum((lot.qty * lot.cost_per_share for lot in lots), Decimal("0"))
    return total_cost / total_qty


def contracts_coverable(unencumbered: Decimal, *, deliverable: int = 100) -> int:
    if unencumbered <= 0 or deliverable <= 0:
        return 0
    return int(unencumbered // Decimal(deliverable))
