"""Typed market-data DTOs with source + observation timestamps."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StockSnapshot(BaseModel):
    """Equity quote / range snapshot from a licensed feed."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    price: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    week52_high: Decimal | None = None
    week52_low: Decimal | None = None
    avg_dollar_volume: Decimal | None = None
    optionable: bool | None = None
    observed_at: datetime
    source: str
    raw: dict[str, Any] = Field(default_factory=dict)


class DividendCalendarEntry(BaseModel):
    """Single declared dividend event with verified ex-date."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    ex_date: date
    record_date: date | None = None
    payable_date: date | None = None
    declaration_date: date | None = None
    amount: Decimal | None = None
    action_type: str = "cash_dividend"
    source: str
    observed_at: datetime
    raw: dict[str, Any] = Field(default_factory=dict)


class DividendCalendarResult(BaseModel):
    """Calendar pull for one or more symbols."""

    model_config = ConfigDict(frozen=True)

    symbols: tuple[str, ...]
    entries: tuple[DividendCalendarEntry, ...]
    source: str
    observed_at: datetime
    window_start: date
    window_end: date
