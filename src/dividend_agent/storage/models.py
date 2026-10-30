"""Persistence row helpers (lightweight; SQL is source of truth)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class HoldingRow:
    account_id: str
    symbol: str
    qty: Decimal
    avg_entry_price: Decimal
    asset_class: str = "us_equity"
    synced_at: datetime | None = None


@dataclass(frozen=True)
class JobRunRow:
    id: int
    job_name: str
    status: str
    started_at: str
    finished_at: str | None
    detail: str
    account_id: str | None
