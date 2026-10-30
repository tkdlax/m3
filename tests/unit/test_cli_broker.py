"""CLI broker/ledger command tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from dividend_agent.brokers.alpaca_paper import AlpacaPaperAdapter
from dividend_agent.brokers.credentials import AlpacaPaperCredentials
from dividend_agent.cli import app

runner = CliRunner()
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _install_mock_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    account = _load("alpaca_paper_account.json")
    positions = _load("alpaca_paper_positions.json")
    orders = _load("alpaca_paper_orders.json")
    activities = _load("alpaca_paper_activities.json")

    def handler(request: httpx.Request) -> httpx.Response:
        mapping = {
            "/v2/account": account,
            "/v2/positions": positions,
            "/v2/orders": orders,
            "/v2/account/activities": activities,
        }
        body = mapping.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, json=body)

    def factory(*, environ: dict[str, str] | None = None) -> AlpacaPaperAdapter:
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

    monkeypatch.setattr("dividend_agent.cli.create_broker_adapter", factory)


def test_verify_paper_fails_without_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_API_SECRET", raising=False)
    result = runner.invoke(app, ["broker", "verify-paper"])
    assert result.exit_code == 1


def test_verify_and_reconcile_with_mock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_mock_adapter(monkeypatch)
    verified = runner.invoke(app, ["broker", "verify-paper"])
    assert verified.exit_code == 0
    assert "verified" in verified.stdout.lower() or "ok" in verified.stdout.lower()

    db = tmp_path / "agent.sqlite3"
    reconciled = runner.invoke(app, ["broker", "reconcile", "--db", str(db)])
    assert reconciled.exit_code == 0
    assert db.exists()

    status = runner.invoke(app, ["broker", "status", "--db", str(db)])
    assert status.exit_code == 0
    assert "paper-acct-001" in status.stdout


def test_ledger_migrate(tmp_path: Path) -> None:
    db = tmp_path / "empty.sqlite3"
    result = runner.invoke(app, ["ledger", "migrate", "--db", str(db)])
    assert result.exit_code == 0
    assert db.exists()
