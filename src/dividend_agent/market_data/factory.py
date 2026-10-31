"""Construct read-only market-data adapters from the environment."""

from __future__ import annotations

import os
from datetime import date, datetime

from dividend_agent.brokers.credentials import load_alpaca_paper_credentials_from_env
from dividend_agent.market_data.alpaca_rest import (
    DEFAULT_DATA_BASE_URL,
    AlpacaMarketDataRestAdapter,
    assert_data_base_url,
)
from dividend_agent.market_data.interface import MarketDataAdapter, MarketDataCapabilityError
from dividend_agent.market_data.types import DividendCalendarResult, StockSnapshot
from dividend_agent.options.chains import OptionChain


class UnconfiguredMarketDataAdapter(MarketDataAdapter):
    """Missing credentials — discover empty; all reads fail closed."""

    def discover_capabilities(self) -> tuple[str, ...]:
        return ()

    def get_stock_snapshot(self, symbol: str) -> StockSnapshot:
        raise MarketDataCapabilityError(
            f"market data unconfigured (set ALPACA_PAPER_API_KEY/SECRET); cannot snapshot {symbol}"
        )

    def get_option_chain(
        self,
        underlying: str,
        *,
        expiration_gte: date | None = None,
        expiration_lte: date | None = None,
        as_of: datetime | None = None,
    ) -> OptionChain:
        raise MarketDataCapabilityError(
            "market data unconfigured; cannot fetch option chain for "
            f"{underlying} without credentials"
        )

    def get_dividend_calendar(
        self,
        symbols: tuple[str, ...] | list[str],
        *,
        window_start: date,
        window_end: date,
    ) -> DividendCalendarResult:
        raise MarketDataCapabilityError("market data unconfigured; cannot fetch dividend calendar")


def create_market_data_adapter(
    *,
    environ: dict[str, str] | None = None,
) -> MarketDataAdapter:
    """Return Alpaca REST market-data adapter when paper creds exist.

    MCP is intentionally not selected here (see ADR 0004): unattended MCP
    schemas for quotes/chains/calendar were not verified; REST data API is.
    """
    env = environ if environ is not None else dict(os.environ)
    creds = load_alpaca_paper_credentials_from_env(environ=env)
    if creds is None:
        return UnconfiguredMarketDataAdapter()
    raw_data = (env.get("ALPACA_DATA_BASE_URL") or DEFAULT_DATA_BASE_URL).strip()
    data_url = assert_data_base_url(raw_data)
    feed = (env.get("ALPACA_OPTION_FEED") or "indicative").strip() or "indicative"
    return AlpacaMarketDataRestAdapter(creds, data_base_url=data_url, option_feed=feed)
