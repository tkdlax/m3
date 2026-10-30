"""Paper credential loading from environment. Live keys/URLs are rejected."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dividend_agent.brokers.interface import BrokerConfigError

DEFAULT_PAPER_BASE_URL = "https://paper-api.alpaca.markets"


@dataclass(frozen=True)
class AlpacaPaperCredentials:
    api_key: str
    api_secret: str
    base_url: str = DEFAULT_PAPER_BASE_URL

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)


def normalize_base_url(url: str) -> str:
    return url.strip().rstrip("/")


def assert_paper_base_url(base_url: str) -> str:
    """Fail closed if the URL looks like Alpaca live trading."""
    normalized = normalize_base_url(base_url)
    lower = normalized.lower()
    if not lower.startswith("https://"):
        raise BrokerConfigError(f"broker base URL must use https; got {normalized!r}")
    # Explicit live host rejection.
    if "api.alpaca.markets" in lower and "paper-api.alpaca.markets" not in lower:
        raise BrokerConfigError(
            "live Alpaca endpoint rejected (paper-only v1): "
            f"{normalized!r}. Use https://paper-api.alpaca.markets"
        )
    if "paper-api.alpaca.markets" in lower:
        return normalized
    if "paper" in lower:
        # Allow test doubles whose host includes 'paper'.
        return normalized
    raise BrokerConfigError(
        "broker base URL is not a recognized Alpaca paper endpoint: "
        f"{normalized!r}. Expected host paper-api.alpaca.markets."
    )


def load_alpaca_paper_credentials_from_env(
    *,
    environ: dict[str, str] | None = None,
) -> AlpacaPaperCredentials | None:
    """Return credentials if both key and secret are set; else None.

    Raises BrokerConfigError if a live base URL is configured or only one of
    key/secret is present.
    """
    env = environ if environ is not None else dict(os.environ)
    key = (env.get("ALPACA_PAPER_API_KEY") or "").strip()
    secret = (env.get("ALPACA_PAPER_API_SECRET") or "").strip()
    raw_url = (env.get("ALPACA_PAPER_BASE_URL") or DEFAULT_PAPER_BASE_URL).strip()
    base_url = assert_paper_base_url(raw_url)

    apca = (env.get("APCA_API_BASE_URL") or "").strip()
    if apca:
        assert_paper_base_url(apca)

    if not key and not secret:
        return None
    if not key or not secret:
        raise BrokerConfigError(
            "both ALPACA_PAPER_API_KEY and ALPACA_PAPER_API_SECRET are required "
            "(or leave both empty for unconfigured fail-closed mode)"
        )
    return AlpacaPaperCredentials(api_key=key, api_secret=secret, base_url=base_url)
