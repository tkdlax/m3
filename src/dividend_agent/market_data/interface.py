"""Read-only market-data port (no order submission)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

from dividend_agent.market_data.types import DividendCalendarResult, StockSnapshot
from dividend_agent.options.chains import OptionChain
from dividend_agent.options.ex_dividend import ExDividendWindow

MARKET_DATA_READ_CAPABILITIES: tuple[str, ...] = (
    "get_stock_snapshot",
    "get_option_chain",
    "get_dividend_calendar",
)

# Explicitly forbidden on any market-data / agent MCP surface.
MARKET_DATA_FORBIDDEN_WRITES: frozenset[str] = frozenset(
    {
        "submit_paper_order",
        "cancel_paper_order",
        "submit_order",
        "place_order",
        "replace_order",
    }
)


class MarketDataError(RuntimeError):
    """Base market-data failure (fail closed)."""


class MarketDataAuthError(MarketDataError):
    """Authentication expired or rejected — no orders may proceed on this path."""


class MarketDataCapabilityError(MarketDataError):
    """Required read capability missing."""


class MarketDataStaleError(MarketDataError):
    """Quote or calendar observation is missing or too old."""


class MarketDataAdapter(ABC):
    """Read-only market data. Implementations must never expose write tools."""

    @abstractmethod
    def discover_capabilities(self) -> tuple[str, ...]:
        """Capability names currently available (empty if unauthenticated)."""

    @abstractmethod
    def get_stock_snapshot(self, symbol: str) -> StockSnapshot:
        """Current equity snapshot with source + observation time."""

    @abstractmethod
    def get_option_chain(
        self,
        underlying: str,
        *,
        expiration_gte: date | None = None,
        expiration_lte: date | None = None,
        as_of: datetime | None = None,
    ) -> OptionChain:
        """Live option chain (calls) for underlying. Production must not use fixtures."""

    @abstractmethod
    def get_dividend_calendar(
        self,
        symbols: tuple[str, ...] | list[str],
        *,
        window_start: date,
        window_end: date,
    ) -> DividendCalendarResult:
        """Declared dividends with ex-dates in/near the window."""

    def next_ex_dividend_window(
        self,
        symbol: str,
        *,
        as_of: date,
        lookback_days: int = 7,
        lookahead_days: int = 90,
    ) -> ExDividendWindow | None:
        """Nearest upcoming (or recent) ex-date for policy checks.

        Returns None only when the calendar was fetched successfully and contains
        no qualifying entry — callers still fail closed under avoid_overlap when
        they need a window and get None for a symbol that should have dividends.
        Raises on auth/capability/stale failures.
        """
        from datetime import timedelta

        start = as_of - timedelta(days=lookback_days)
        end = as_of + timedelta(days=lookahead_days)
        result = self.get_dividend_calendar((symbol,), window_start=start, window_end=end)
        upcoming = [
            e for e in result.entries if e.symbol.upper() == symbol.upper() and e.ex_date >= as_of
        ]
        upcoming.sort(key=lambda e: e.ex_date)
        if upcoming:
            e = upcoming[0]
            return ExDividendWindow(ex_date=e.ex_date, source=e.source, declared=True)
        # Also accept the most recent past ex-date within lookback for overlap checks
        recent = [
            e for e in result.entries if e.symbol.upper() == symbol.upper() and e.ex_date < as_of
        ]
        recent.sort(key=lambda e: e.ex_date, reverse=True)
        if recent:
            e = recent[0]
            return ExDividendWindow(ex_date=e.ex_date, source=e.source, declared=True)
        return None


def require_market_data_capabilities(
    adapter: MarketDataAdapter,
    required: tuple[str, ...] = MARKET_DATA_READ_CAPABILITIES,
) -> None:
    caps = set(adapter.discover_capabilities())
    missing = [c for c in required if c not in caps]
    if missing:
        raise MarketDataCapabilityError(
            "market-data capabilities missing (fail closed, no orders): " + ", ".join(missing)
        )
    banned = sorted(caps & MARKET_DATA_FORBIDDEN_WRITES)
    if banned:
        raise MarketDataCapabilityError(
            "market-data adapter must remain read-only; forbidden writes present: "
            + ", ".join(banned)
        )
