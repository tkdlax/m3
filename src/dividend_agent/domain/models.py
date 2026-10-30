"""Core typed domain models for M0 skeleton.

M0 defines shapes only. Broker adapters, order submission, and probability
engines land in later milestones. No live-trading types are introduced.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class OperatingMode(StrEnum):
    """Application operating modes (spec §4)."""

    RESEARCH_ONLY = "research_only"
    PAPER_DRY_RUN = "paper_dry_run"
    PAPER_AUTO = "paper_auto"


class BrokerEnvironment(StrEnum):
    """Allowed brokerage environments. Live is intentionally absent in v1."""

    PAPER = "paper"


class PaperVerificationStatus(StrEnum):
    """Result of paper-account attestation."""

    UNVERIFIED = "unverified"
    VERIFIED_PAPER = "verified_paper"
    FAILED = "failed"


class Money(BaseModel):
    """Decimal money amount; avoid float for prices and notionals."""

    model_config = ConfigDict(frozen=True)

    amount: Decimal
    currency: str = "USD"


class TradeIntent(BaseModel):
    """Proposed trade before validation/execution (spec §8).

    M0 persists/validates structure only; submission is not implemented.
    """

    model_config = ConfigDict(frozen=True)

    intent_id: UUID = Field(default_factory=uuid4)
    account_id: str
    symbol: str
    instrument_id: str | None = None
    side: str
    quantity: Decimal
    order_type: str
    limit_price: Decimal | None = None
    expiry: datetime | None = None
    strike: Decimal | None = None
    deliverable_shares: int | None = None
    market_snapshot_at: datetime | None = None
    evidence_refs: tuple[str, ...] = ()
    rule_config_version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    extras: dict[str, Any] = Field(default_factory=dict)
