"""Paper credential / URL fail-closed tests."""

from __future__ import annotations

import pytest

from dividend_agent.brokers.credentials import (
    assert_paper_base_url,
    load_alpaca_paper_credentials_from_env,
)
from dividend_agent.brokers.interface import BrokerConfigError


def test_rejects_live_alpaca_url() -> None:
    with pytest.raises(BrokerConfigError, match="live"):
        assert_paper_base_url("https://api.alpaca.markets")


def test_accepts_paper_url() -> None:
    assert assert_paper_base_url("https://paper-api.alpaca.markets/") == (
        "https://paper-api.alpaca.markets"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://paper.example.com",
        "https://paper-api.alpaca.markets.evil.com",
        "https://user:pass@paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets:8443",
        "https://paper-api.alpaca.markets/v2",
        "http://paper-api.alpaca.markets",
        "https://api.alpaca.markets",
        "https://not-paper-api.alpaca.markets",
    ],
)
def test_rejects_lookalike_and_malformed_urls(url: str) -> None:
    with pytest.raises(BrokerConfigError):
        assert_paper_base_url(url)


def test_partial_creds_fail() -> None:
    with pytest.raises(BrokerConfigError, match="both"):
        load_alpaca_paper_credentials_from_env(
            environ={
                "ALPACA_PAPER_API_KEY": "only-key",
                "ALPACA_PAPER_API_SECRET": "",
            }
        )


def test_empty_creds_return_none() -> None:
    assert load_alpaca_paper_credentials_from_env(environ={}) is None


def test_loads_paper_creds() -> None:
    creds = load_alpaca_paper_credentials_from_env(
        environ={
            "ALPACA_PAPER_API_KEY": "PKTEST",
            "ALPACA_PAPER_API_SECRET": "secret",
            "ALPACA_PAPER_BASE_URL": "https://paper-api.alpaca.markets",
        }
    )
    assert creds is not None
    assert creds.api_key == "PKTEST"
