"""Alpaca paper REST adapter unit tests with httpx mock transport."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from dividend_agent.brokers.alpaca_paper import AlpacaPaperAdapter
from dividend_agent.brokers.credentials import AlpacaPaperCredentials
from dividend_agent.brokers.interface import PaperNotVerifiedError
from dividend_agent.domain.models import PaperVerificationStatus

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _mock_transport() -> httpx.MockTransport:
    account = _load("alpaca_paper_account.json")
    positions = _load("alpaca_paper_positions.json")
    orders = _load("alpaca_paper_orders.json")
    activities = _load("alpaca_paper_activities.json")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/account":
            return httpx.Response(200, json=account)
        if path == "/v2/positions":
            return httpx.Response(200, json=positions)
        if path == "/v2/orders":
            return httpx.Response(200, json=orders)
        if path == "/v2/account/activities":
            return httpx.Response(200, json=activities)
        return httpx.Response(404, json={"message": "not found"})

    return httpx.MockTransport(handler)


def _adapter() -> AlpacaPaperAdapter:
    creds = AlpacaPaperCredentials(
        api_key="PKTEST",
        api_secret="secret",
        base_url="https://paper-api.alpaca.markets",
    )
    client = httpx.Client(
        transport=_mock_transport(),
        base_url=creds.base_url,
        headers={
            "APCA-API-KEY-ID": creds.api_key,
            "APCA-API-SECRET-KEY": creds.api_secret,
        },
    )
    return AlpacaPaperAdapter(creds, client=client)


def test_verify_paper_success() -> None:
    adapter = _adapter()
    result = adapter.verify_paper()
    assert result.status == PaperVerificationStatus.VERIFIED_PAPER
    assert result.account_id == "paper-acct-001"
    assert "get_account" in result.capabilities


def test_reads_positions_orders_activities() -> None:
    adapter = _adapter()
    adapter.verify_paper()
    account = adapter.get_account()
    assert account.cash > 0
    positions = adapter.list_positions()
    assert len(positions) == 2
    assert positions[0].symbol == "GIS"
    orders = adapter.list_orders()
    assert orders[0].broker_order_id == "ord-001"
    activities = adapter.list_activities()
    assert {a.activity_type for a in activities} >= {"FILL", "DIV"}


def test_unverified_reads_fail_closed_on_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    creds = AlpacaPaperCredentials(
        api_key="bad",
        api_secret="bad",
        base_url="https://paper-api.alpaca.markets",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=creds.base_url,
    )
    adapter = AlpacaPaperAdapter(creds, client=client)
    result = adapter.verify_paper()
    assert result.status == PaperVerificationStatus.FAILED
    with pytest.raises(PaperNotVerifiedError):
        adapter.get_account()
