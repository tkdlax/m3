"""Alpaca paper Trading API adapter (REST). No order submission in M1."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from dividend_agent.brokers.credentials import (
    AlpacaPaperCredentials,
    assert_paper_base_url,
)
from dividend_agent.brokers.interface import (
    M1_READ_CAPABILITIES,
    BrokerAdapter,
    BrokerConfigError,
    BrokerTransportError,
    PaperVerificationResult,
)
from dividend_agent.brokers.types import (
    AccountSnapshot,
    ActivitySnapshot,
    OrderSnapshot,
    PositionSnapshot,
)
from dividend_agent.domain.models import PaperVerificationStatus


def _dec(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def _dec_opt(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


class AlpacaPaperAdapter(BrokerAdapter):
    """Read-only Alpaca paper REST client.

    Preferred long-term interface remains Alpaca MCP (see alpaca_mcp.py) once
    capability discovery confirms unattended coverage. M1 uses REST so paper
    verify + ledger work without an MCP runtime.
    """

    def __init__(
        self,
        credentials: AlpacaPaperCredentials,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._credentials = credentials
        self._base_url = assert_paper_base_url(credentials.base_url)
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self._base_url,
            headers={
                "APCA-API-KEY-ID": credentials.api_key,
                "APCA-API-SECRET-KEY": credentials.api_secret,
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        self._verified: PaperVerificationResult | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> AlpacaPaperAdapter:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def discover_capabilities(self) -> tuple[str, ...]:
        return M1_READ_CAPABILITIES

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise BrokerTransportError(f"Alpaca request failed: {exc}") from exc
        if response.status_code == 401:
            raise BrokerTransportError("Alpaca authentication failed (401)")
        if response.status_code == 403:
            raise BrokerTransportError("Alpaca forbidden (403)")
        if response.status_code >= 400:
            detail = response.text[:500]
            raise BrokerTransportError(f"Alpaca HTTP {response.status_code} for {path}: {detail}")
        if not response.content:
            return None
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise BrokerTransportError(f"invalid JSON from Alpaca {path}") from exc

    def verify_paper(self) -> PaperVerificationResult:
        try:
            assert_paper_base_url(self._base_url)
        except BrokerConfigError as exc:
            result = PaperVerificationResult(
                status=PaperVerificationStatus.FAILED,
                detail=str(exc),
            )
            self._verified = result
            return result

        try:
            payload = self._request("GET", "/v2/account")
        except BrokerTransportError as exc:
            result = PaperVerificationResult(
                status=PaperVerificationStatus.FAILED,
                detail=str(exc),
            )
            self._verified = result
            return result

        if not isinstance(payload, dict):
            result = PaperVerificationResult(
                status=PaperVerificationStatus.FAILED,
                detail="unexpected /v2/account payload",
            )
            self._verified = result
            return result

        account_id = str(payload.get("id") or payload.get("account_number") or "")
        status = str(payload.get("status") or "")
        # Positive attestation: paper base URL + authenticated account payload.
        result = PaperVerificationResult(
            status=PaperVerificationStatus.VERIFIED_PAPER,
            account_id=account_id or None,
            detail=(
                f"Verified Alpaca paper account via REST (status={status!r}, base={self._base_url})"
            ),
            capabilities=self.discover_capabilities(),
        )
        self._verified = result
        return result

    def _require_verified(self) -> PaperVerificationResult:
        if self._verified is None:
            self.verify_paper()
        assert self._verified is not None
        if not self._verified.is_verified_paper:
            from dividend_agent.brokers.interface import PaperNotVerifiedError

            raise PaperNotVerifiedError(self._verified)
        return self._verified

    def get_account(self) -> AccountSnapshot:
        self._require_verified()
        payload = self._request("GET", "/v2/account")
        if not isinstance(payload, dict):
            raise BrokerTransportError("unexpected /v2/account payload")
        return AccountSnapshot(
            account_id=str(payload.get("id") or ""),
            status=str(payload.get("status") or ""),
            currency=str(payload.get("currency") or "USD"),
            cash=_dec(payload.get("cash")),
            buying_power=_dec(payload.get("buying_power")),
            equity=_dec(payload.get("equity")),
            portfolio_value=_dec(payload.get("portfolio_value")),
            pattern_day_trader=bool(payload.get("pattern_day_trader")),
            trading_blocked=bool(payload.get("trading_blocked")),
            account_blocked=bool(payload.get("account_blocked")),
            options_approved_level=(
                int(payload["options_approved_level"])
                if payload.get("options_approved_level") is not None
                else None
            ),
            raw=payload,
        )

    def list_positions(self) -> list[PositionSnapshot]:
        self._require_verified()
        payload = self._request("GET", "/v2/positions")
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise BrokerTransportError("unexpected /v2/positions payload")
        out: list[PositionSnapshot] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            out.append(
                PositionSnapshot(
                    symbol=str(item.get("symbol") or ""),
                    asset_class=str(item.get("asset_class") or "us_equity"),
                    qty=_dec(item.get("qty")),
                    qty_available=_dec_opt(item.get("qty_available")),
                    avg_entry_price=_dec(item.get("avg_entry_price")),
                    market_value=_dec_opt(item.get("market_value")),
                    cost_basis=_dec_opt(item.get("cost_basis")),
                    side=str(item.get("side") or "long"),
                    asset_id=str(item["asset_id"]) if item.get("asset_id") else None,
                    raw=item,
                )
            )
        return out

    def list_orders(self, *, status: str = "all", limit: int = 100) -> list[OrderSnapshot]:
        self._require_verified()
        payload = self._request(
            "GET",
            "/v2/orders",
            params={"status": status, "limit": limit, "direction": "desc"},
        )
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise BrokerTransportError("unexpected /v2/orders payload")
        out: list[OrderSnapshot] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            out.append(
                OrderSnapshot(
                    broker_order_id=str(item.get("id") or ""),
                    client_order_id=(
                        str(item["client_order_id"]) if item.get("client_order_id") else None
                    ),
                    symbol=str(item.get("symbol") or ""),
                    side=str(item.get("side") or ""),
                    order_type=str(item.get("type") or item.get("order_type") or ""),
                    status=str(item.get("status") or ""),
                    qty=_dec_opt(item.get("qty")),
                    filled_qty=_dec_opt(item.get("filled_qty")),
                    limit_price=_dec_opt(item.get("limit_price")),
                    filled_avg_price=_dec_opt(item.get("filled_avg_price")),
                    asset_class=(str(item["asset_class"]) if item.get("asset_class") else None),
                    submitted_at=_parse_dt(item.get("submitted_at")),
                    updated_at=_parse_dt(item.get("updated_at")),
                    raw=item,
                )
            )
        return out

    def list_activities(
        self,
        *,
        activity_types: tuple[str, ...] | None = None,
        page_size: int = 100,
    ) -> list[ActivitySnapshot]:
        self._require_verified()
        params: dict[str, Any] = {"page_size": page_size, "direction": "desc"}
        if activity_types:
            params["activity_types"] = ",".join(activity_types)
        payload = self._request("GET", "/v2/account/activities", params=params)
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise BrokerTransportError("unexpected /v2/account/activities payload")
        out: list[ActivitySnapshot] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            activity_id = str(
                item.get("id") or item.get("activity_id") or item.get("transaction_time") or ""
            )
            out.append(
                ActivitySnapshot(
                    activity_id=activity_id,
                    activity_type=str(item.get("activity_type") or item.get("type") or ""),
                    symbol=str(item["symbol"]) if item.get("symbol") else None,
                    side=str(item["side"]) if item.get("side") else None,
                    qty=_dec_opt(item.get("qty")),
                    price=_dec_opt(item.get("price")),
                    net_amount=_dec_opt(item.get("net_amount")),
                    transaction_time=_parse_dt(item.get("transaction_time") or item.get("date")),
                    order_id=str(item["order_id"]) if item.get("order_id") else None,
                    raw=item,
                )
            )
        return out
