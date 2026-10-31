"""Reject stale or missing market-data observations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dividend_agent.market_data.interface import MarketDataStaleError
from dividend_agent.market_data.types import DividendCalendarEntry, StockSnapshot
from dividend_agent.options.chains import OptionChain
from dividend_agent.options.contract_spec import OptionQuote


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def assert_fresh_datetime(
    observed_at: datetime | None,
    *,
    max_age_seconds: int,
    label: str,
    now: datetime | None = None,
) -> datetime:
    if observed_at is None:
        raise MarketDataStaleError(f"{label}: missing observation timestamp")
    ts = observed_at if observed_at.tzinfo is not None else observed_at.replace(tzinfo=UTC)
    current = _now(now)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    age = current - ts
    if age > timedelta(seconds=max_age_seconds):
        raise MarketDataStaleError(
            f"{label}: stale observation age={age.total_seconds():.0f}s "
            f"> max={max_age_seconds}s (observed_at={ts.isoformat()})"
        )
    if age < timedelta(seconds=-300):
        # Clock skew / future timestamps more than 5 minutes ahead
        raise MarketDataStaleError(
            f"{label}: observation timestamp in the future: {ts.isoformat()}"
        )
    return ts


def assert_stock_snapshot_fresh(
    snap: StockSnapshot,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> None:
    if snap.price is None and snap.bid is None and snap.ask is None:
        raise MarketDataStaleError(f"stock snapshot {snap.symbol}: missing price/quote")
    assert_fresh_datetime(
        snap.observed_at,
        max_age_seconds=max_age_seconds,
        label=f"stock snapshot {snap.symbol}",
        now=now,
    )


def assert_quote_fresh(
    quote: OptionQuote,
    *,
    max_age_seconds: int,
    symbol: str,
    now: datetime | None = None,
) -> None:
    assert_fresh_datetime(
        quote.quote_as_of,
        max_age_seconds=max_age_seconds,
        label=f"option quote {symbol}",
        now=now,
    )


def assert_chain_fresh(
    chain: OptionChain,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> None:
    if not chain.entries:
        raise MarketDataStaleError(f"option chain {chain.underlying}: empty")
    if chain.source == "fixture":
        raise MarketDataStaleError(
            f"option chain {chain.underlying}: fixture source forbidden on production path"
        )
    # Require at least one fresh quote; reject if all stale/missing.
    errors: list[str] = []
    ok = 0
    for entry in chain.entries:
        try:
            assert_quote_fresh(
                entry.quote,
                max_age_seconds=max_age_seconds,
                symbol=entry.contract.symbol,
                now=now,
            )
            ok += 1
        except MarketDataStaleError as exc:
            errors.append(str(exc))
    if ok == 0:
        detail = errors[0] if errors else "no quotes"
        raise MarketDataStaleError(f"option chain {chain.underlying}: no fresh quotes ({detail})")


def assert_dividend_entry_complete(entry: DividendCalendarEntry) -> None:
    if not entry.source:
        raise MarketDataStaleError(f"dividend {entry.symbol}: missing source")
    # ex_date / observed_at are required fields on the model; keep guard for raw callers.
    _ = entry.ex_date
    _ = entry.observed_at
