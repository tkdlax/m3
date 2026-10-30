"""Reconciliation writes broker snapshots into SQLite without submitting orders."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from dividend_agent.brokers.alpaca_paper import AlpacaPaperAdapter
from dividend_agent.brokers.credentials import AlpacaPaperCredentials
from dividend_agent.brokers.interface import PaperNotVerifiedError, UnconfiguredBrokerAdapter
from dividend_agent.execution.reconciliation import Reconciler
from dividend_agent.storage import LedgerRepository, open_migrated

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _paper_adapter() -> AlpacaPaperAdapter:
    account = _load("alpaca_paper_account.json")
    positions = _load("alpaca_paper_positions.json")
    orders = _load("alpaca_paper_orders.json")
    activities = _load("alpaca_paper_activities.json")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        mapping = {
            "/v2/account": account,
            "/v2/positions": positions,
            "/v2/orders": orders,
            "/v2/account/activities": activities,
        }
        if path in mapping:
            return httpx.Response(200, json=mapping[path])
        return httpx.Response(404)

    creds = AlpacaPaperCredentials(
        api_key="PKTEST",
        api_secret="secret",
        base_url="https://paper-api.alpaca.markets",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=creds.base_url,
    )
    return AlpacaPaperAdapter(creds, client=client)


def test_reconcile_persists_broker_state(tmp_path: Path) -> None:
    conn = open_migrated(tmp_path / "agent.sqlite3")
    try:
        repo = LedgerRepository(conn)
        result = Reconciler(_paper_adapter(), repo).reconcile()
        assert result.account_id == "paper-acct-001"
        assert result.positions == 2
        assert result.orders == 1
        assert result.activities == 2
        assert not result.mismatches
        holdings = repo.list_holdings(result.account_id)
        assert {h.symbol for h in holdings} == {"GIS", "T"}
        assert repo.count_orders(result.account_id) == 1
        assert repo.count_activities(result.account_id) == 2
        job = repo.latest_job("reconcile")
        assert job is not None and job.status == "completed"
    finally:
        conn.close()


def test_reconcile_fails_closed_without_paper(tmp_path: Path) -> None:
    conn = open_migrated(tmp_path / "agent.sqlite3")
    try:
        with pytest.raises(PaperNotVerifiedError):
            Reconciler(UnconfiguredBrokerAdapter(), LedgerRepository(conn)).reconcile()
    finally:
        conn.close()


def test_reconcile_never_calls_order_submit(tmp_path: Path) -> None:
    """Guard: M1 reconciler must not invoke any submit/cancel methods."""
    adapter = _paper_adapter()
    adapter.submit_order = MagicMock()  # type: ignore[attr-defined]
    adapter.cancel_order = MagicMock()  # type: ignore[attr-defined]
    conn = open_migrated(tmp_path / "agent.sqlite3")
    try:
        Reconciler(adapter, LedgerRepository(conn)).reconcile()
    finally:
        conn.close()
    adapter.submit_order.assert_not_called()  # type: ignore[attr-defined]
    adapter.cancel_order.assert_not_called()  # type: ignore[attr-defined]
