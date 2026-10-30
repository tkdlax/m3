"""Broker read-model DTOs used by adapters and the ledger (M1)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AccountSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    status: str
    currency: str = "USD"
    cash: Decimal
    buying_power: Decimal
    equity: Decimal
    portfolio_value: Decimal
    pattern_day_trader: bool = False
    trading_blocked: bool = False
    account_blocked: bool = False
    options_approved_level: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class PositionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    asset_class: str = "us_equity"
    qty: Decimal
    qty_available: Decimal | None = None
    avg_entry_price: Decimal
    market_value: Decimal | None = None
    cost_basis: Decimal | None = None
    side: str = "long"
    asset_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class OrderSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    broker_order_id: str
    client_order_id: str | None = None
    symbol: str
    side: str
    order_type: str
    status: str
    qty: Decimal | None = None
    filled_qty: Decimal | None = None
    limit_price: Decimal | None = None
    filled_avg_price: Decimal | None = None
    asset_class: str | None = None
    submitted_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ActivitySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    activity_id: str
    activity_type: str
    symbol: str | None = None
    side: str | None = None
    qty: Decimal | None = None
    price: Decimal | None = None
    net_amount: Decimal | None = None
    transaction_time: datetime | None = None
    order_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
