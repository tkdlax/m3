"""Option contract / deliverable validation (spec §8)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OptionRight(StrEnum):
    CALL = "call"
    PUT = "put"


class ContractValidationError(ValueError):
    """Contract fails hard validation."""


class OptionContract(BaseModel):
    """Standard U.S. equity option contract snapshot."""

    model_config = ConfigDict(frozen=True)

    symbol: str  # option OCC symbol or broker id
    underlying: str
    expiration: date
    strike: Decimal
    right: OptionRight
    multiplier: int = 100
    deliverable_shares: int = 100
    adjusted: bool = False
    tradable: bool = True
    status: str = "active"
    instrument_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("strike", mode="before")
    @classmethod
    def _dec_strike(cls, value: object) -> Decimal:
        return Decimal(str(value))

    @model_validator(mode="after")
    def _validate_shape(self) -> OptionContract:
        if self.strike <= 0:
            raise ContractValidationError("strike must be positive")
        if self.multiplier <= 0:
            raise ContractValidationError("multiplier must be positive")
        if self.deliverable_shares <= 0:
            raise ContractValidationError("deliverable_shares must be positive")
        return self


class OptionQuote(BaseModel):
    model_config = ConfigDict(frozen=True)

    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    bid_size: int | None = None
    ask_size: int | None = None
    volume: int | None = None
    open_interest: int | None = None
    implied_volatility: Decimal | None = None
    quote_as_of: datetime | None = None

    @property
    def premium_per_share_conservative(self) -> Decimal | None:
        """Executable short-call premium estimate: prefer bid (spec §9)."""
        if self.bid is not None and self.bid > 0:
            return self.bid
        return None

    @property
    def premium_per_contract(self) -> Decimal | None:
        pps = self.premium_per_share_conservative
        if pps is None:
            return None
        return pps * Decimal("100")

    @property
    def bid_ask_spread_pct(self) -> Decimal | None:
        if self.bid is None or self.ask is None or self.ask <= 0:
            return None
        if self.bid < 0:
            return None
        mid = (self.bid + self.ask) / Decimal("2")
        if mid <= 0:
            return None
        return (self.ask - self.bid) / mid


def validate_standard_covered_call(contract: OptionContract) -> list[str]:
    """Return hard-block reasons; empty means contract shape is acceptable for v1."""
    reasons: list[str] = []
    if contract.right != OptionRight.CALL:
        reasons.append("not_a_call")
    if contract.adjusted:
        reasons.append("adjusted_contract_unsupported")
    if contract.deliverable_shares != 100 or contract.multiplier != 100:
        reasons.append("nonstandard_deliverable_unsupported")
    if not contract.tradable or contract.status.lower() not in {"active", "tradable", "open"}:
        reasons.append("contract_not_tradable")
    return reasons


def parse_contract(payload: dict[str, Any]) -> OptionContract:
    """Parse a broker/fixture contract dict into OptionContract."""
    right_raw = str(payload.get("right") or payload.get("type") or "call").lower()
    right = OptionRight.CALL if right_raw.startswith("c") else OptionRight.PUT
    exp_raw = payload.get("expiration") or payload.get("expiry")
    if isinstance(exp_raw, datetime):
        expiration = exp_raw.date()
    else:
        expiration = date.fromisoformat(str(exp_raw)[:10])
    deliverable = int(payload.get("deliverable_shares") or payload.get("size") or 100)
    multiplier = int(payload.get("multiplier") or 100)
    adjusted = bool(payload.get("adjusted") or payload.get("is_adjusted") or False)
    if deliverable != 100 or multiplier != 100:
        adjusted = True
    return OptionContract(
        symbol=str(payload.get("symbol") or payload.get("occ_symbol") or ""),
        underlying=str(payload.get("underlying") or payload.get("underlying_symbol") or ""),
        expiration=expiration,
        strike=Decimal(str(payload["strike"])),
        right=right,
        multiplier=multiplier,
        deliverable_shares=deliverable,
        adjusted=adjusted,
        tradable=bool(payload.get("tradable", True)),
        status=str(payload.get("status") or "active"),
        instrument_id=(str(payload["instrument_id"]) if payload.get("instrument_id") else None),
        raw=payload,
    )


def parse_quote(payload: dict[str, Any]) -> OptionQuote:
    def _d(key: str) -> Decimal | None:
        if payload.get(key) is None or payload.get(key) == "":
            return None
        return Decimal(str(payload[key]))

    as_of = payload.get("quote_as_of") or payload.get("as_of") or payload.get("timestamp")
    quote_as_of = None
    if as_of:
        text = str(as_of).replace("Z", "+00:00")
        quote_as_of = datetime.fromisoformat(text)
    return OptionQuote(
        bid=_d("bid"),
        ask=_d("ask"),
        last=_d("last"),
        bid_size=int(payload["bid_size"]) if payload.get("bid_size") is not None else None,
        ask_size=int(payload["ask_size"]) if payload.get("ask_size") is not None else None,
        volume=int(payload["volume"]) if payload.get("volume") is not None else None,
        open_interest=(
            int(payload["open_interest"]) if payload.get("open_interest") is not None else None
        ),
        implied_volatility=_d("implied_volatility") or _d("iv"),
        quote_as_of=quote_as_of,
    )
