"""Construct the appropriate paper broker adapter from the environment."""

from __future__ import annotations

from dividend_agent.brokers.alpaca_paper import AlpacaPaperAdapter
from dividend_agent.brokers.credentials import load_alpaca_paper_credentials_from_env
from dividend_agent.brokers.interface import BrokerAdapter, UnconfiguredBrokerAdapter


def create_broker_adapter(
    *,
    environ: dict[str, str] | None = None,
) -> BrokerAdapter:
    """Return Alpaca paper REST adapter when creds exist; else unconfigured stub."""
    creds = load_alpaca_paper_credentials_from_env(environ=environ)
    if creds is None:
        return UnconfiguredBrokerAdapter()
    return AlpacaPaperAdapter(creds)
