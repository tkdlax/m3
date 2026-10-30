"""Contract-style checks that fixture payloads map to typed snapshots."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from dividend_agent.brokers.alpaca_paper import AlpacaPaperAdapter
from dividend_agent.brokers.credentials import AlpacaPaperCredentials

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_fixture_shapes_parse() -> None:
    account = json.loads((FIXTURES / "alpaca_paper_account.json").read_text())
    positions = json.loads((FIXTURES / "alpaca_paper_positions.json").read_text())
    orders = json.loads((FIXTURES / "alpaca_paper_orders.json").read_text())
    activities = json.loads((FIXTURES / "alpaca_paper_activities.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        return {
            "/v2/account": httpx.Response(200, json=account),
            "/v2/positions": httpx.Response(200, json=positions),
            "/v2/orders": httpx.Response(200, json=orders),
            "/v2/account/activities": httpx.Response(200, json=activities),
        }.get(request.url.path, httpx.Response(404))

    creds = AlpacaPaperCredentials("k", "s", "https://paper-api.alpaca.markets")
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url=creds.base_url)
    adapter = AlpacaPaperAdapter(creds, client=client)
    assert adapter.verify_paper().is_verified_paper
    snap = adapter.get_account()
    assert snap.account_id == "paper-acct-001"
    assert all(p.qty > 0 for p in adapter.list_positions())
    assert adapter.list_orders()[0].status == "filled"
    assert adapter.list_activities()[0].activity_id == "act-001"
