"""Read-only market data: quotes, option chains, dividend calendar (Milestone B)."""

from dividend_agent.market_data.factory import create_market_data_adapter
from dividend_agent.market_data.interface import (
    MARKET_DATA_READ_CAPABILITIES,
    MarketDataAdapter,
    MarketDataAuthError,
    MarketDataCapabilityError,
    MarketDataError,
    MarketDataStaleError,
    require_market_data_capabilities,
)
from dividend_agent.market_data.types import (
    DividendCalendarEntry,
    DividendCalendarResult,
    StockSnapshot,
)

__all__ = [
    "MARKET_DATA_READ_CAPABILITIES",
    "DividendCalendarEntry",
    "DividendCalendarResult",
    "MarketDataAdapter",
    "MarketDataAuthError",
    "MarketDataCapabilityError",
    "MarketDataError",
    "MarketDataStaleError",
    "StockSnapshot",
    "create_market_data_adapter",
    "require_market_data_capabilities",
]
