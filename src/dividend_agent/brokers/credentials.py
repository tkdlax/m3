"""Paper credential loading from environment. Live keys/URLs are rejected."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dividend_agent.brokers.interface import BrokerConfigError

DEFAULT_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
CANONICAL_PAPER_HOST = "paper-api.alpaca.markets"


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
    """Fail closed unless the URL is exactly the Alpaca paper REST endpoint.

    Validates scheme, hostname, port, userinfo, and path. Mock/test transports
    must not use this helper — they bypass HTTP entirely (``FakePaperBroker``).
    """
    normalized = normalize_base_url(base_url)
    parsed = urlparse(normalized)

    if parsed.scheme.lower() != "https":
        raise BrokerConfigError(f"broker base URL must use https; got {normalized!r}")
    if parsed.username is not None or parsed.password is not None:
        raise BrokerConfigError(f"broker base URL must not include userinfo: {normalized!r}")
    if parsed.query or parsed.fragment:
        raise BrokerConfigError(f"broker base URL must not include query/fragment: {normalized!r}")

    host = (parsed.hostname or "").lower()
    if not host:
        raise BrokerConfigError(f"broker base URL missing hostname: {normalized!r}")

    # Exact paper host only — no substring / lookalike hosts.
    if host == "api.alpaca.markets":
        raise BrokerConfigError(
            "live Alpaca endpoint rejected (paper-only v1): "
            f"{normalized!r}. Use https://paper-api.alpaca.markets"
        )
    if host != CANONICAL_PAPER_HOST:
        raise BrokerConfigError(
            "broker base URL is not the exact Alpaca paper endpoint: "
            f"{normalized!r}. Expected host {CANONICAL_PAPER_HOST}."
        )

    if parsed.port is not None and parsed.port != 443:
        raise BrokerConfigError(
            f"broker base URL must use default https port 443; got {normalized!r}"
        )

    path = parsed.path or ""
    if path not in {"", "/"}:
        # Allow only empty path on the paper host (API paths are appended by client).
        raise BrokerConfigError(
            f"broker base URL must not include an unexpected path: {normalized!r}"
        )

    return f"https://{CANONICAL_PAPER_HOST}"


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
