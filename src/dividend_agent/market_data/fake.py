"""In-memory market-data adapter for tests (replay fixtures only)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from dividend_agent.market_data.interface import (
    MARKET_DATA_READ_CAPABILITIES,
    MarketDataAdapter,
    MarketDataAuthError,
    MarketDataCapabilityError,
)
from dividend_agent.market_data.types import (
    DividendCalendarEntry,
    DividendCalendarResult,
    StockSnapshot,
)
from dividend_agent.options.chains import OptionChain


class FakeMarketDataAdapter(MarketDataAdapter):
    def __init__(
        self,
        *,
        chain: OptionChain | None = None,
        snapshot: StockSnapshot | None = None,
        dividends: list[DividendCalendarEntry] | None = None,
        capabilities: tuple[str, ...] | None = None,
        auth_expired: bool = False,
    ) -> None:
        self._chain = chain
        self._snapshot = snapshot
        self._dividends = list(dividends or [])
        self._capabilities = MARKET_DATA_READ_CAPABILITIES if capabilities is None else capabilities
        self._auth_expired = auth_expired

    def discover_capabilities(self) -> tuple[str, ...]:
        if self._auth_expired:
            return ()
        return self._capabilities

    def _ensure_auth(self) -> None:
        if self._auth_expired:
            raise MarketDataAuthError("fake market-data auth expired")

    def get_stock_snapshot(self, symbol: str) -> StockSnapshot:
        self._ensure_auth()
        if "get_stock_snapshot" not in self.discover_capabilities():
            raise MarketDataCapabilityError("get_stock_snapshot unavailable")
        if self._snapshot is not None and self._snapshot.symbol.upper() == symbol.upper():
            return self._snapshot
        return StockSnapshot(
            symbol=symbol.upper(),
            price=Decimal("40"),
            observed_at=datetime.now(UTC),
            source="fake_market_data",
        )

    def get_option_chain(
        self,
        underlying: str,
        *,
        expiration_gte: date | None = None,
        expiration_lte: date | None = None,
        as_of: datetime | None = None,
    ) -> OptionChain:
        self._ensure_auth()
        if "get_option_chain" not in self.discover_capabilities():
            raise MarketDataCapabilityError("get_option_chain unavailable")
        if self._chain is None:
            raise MarketDataCapabilityError(f"no chain configured for {underlying}")
        if self._chain.underlying.upper() != underlying.upper():
            raise MarketDataCapabilityError(
                f"chain underlying mismatch {self._chain.underlying}!={underlying}"
            )
        return self._chain

    def get_dividend_calendar(
        self,
        symbols: tuple[str, ...] | list[str],
        *,
        window_start: date,
        window_end: date,
    ) -> DividendCalendarResult:
        self._ensure_auth()
        if "get_dividend_calendar" not in self.discover_capabilities():
            raise MarketDataCapabilityError("get_dividend_calendar unavailable")
        syms = tuple(s.upper() for s in symbols)
        entries = [
            e
            for e in self._dividends
            if e.symbol.upper() in syms and window_start <= e.ex_date <= window_end
        ]
        return DividendCalendarResult(
            symbols=syms,
            entries=tuple(entries),
            source="fake_market_data",
            observed_at=datetime.now(UTC),
            window_start=window_start,
            window_end=window_end,
        )


def fresh_gis_chain_from_fixture(path: str, *, now: datetime | None = None) -> OptionChain:
    """Load fixture chain but rewrite source/timestamps for live-path unit tests."""
    from dividend_agent.options.chains import load_chain_json
    from dividend_agent.options.contract_spec import OptionQuote

    chain = load_chain_json(path)
    observed = now or datetime.now(UTC)
    entries = []
    for entry in chain.entries:
        quote = entry.quote.model_copy(update={"quote_as_of": observed})
        # Ensure OptionQuote type
        assert isinstance(quote, OptionQuote)
        entries.append(entry.model_copy(update={"quote": quote}))
    return chain.model_copy(
        update={
            "entries": tuple(entries),
            "source": "fake_market_data",
            "as_of": observed.date(),
        }
    )


def sample_dividend(symbol: str = "GIS", *, days_ahead: int = 45) -> DividendCalendarEntry:
    return DividendCalendarEntry(
        symbol=symbol,
        ex_date=date.today() + timedelta(days=days_ahead),
        amount=Decimal("0.50"),
        source="fake_market_data",
        observed_at=datetime.now(UTC),
    )
