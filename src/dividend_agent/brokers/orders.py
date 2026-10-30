"""Paper order request DTOs (submit path; never live)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PaperOrderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    client_order_id: str
    symbol: str
    side: str  # buy | sell
    qty: Decimal
    order_type: str = "limit"
    time_in_force: str = "day"
    limit_price: Decimal | None = None
    asset_class: str | None = None  # us_equity | us_option
    extras: dict[str, Any] = Field(default_factory=dict)
