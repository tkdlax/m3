"""Production helpers: fetch live chain + ex-div for evaluation (no fixtures)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from dividend_agent.config import AppConfig
from dividend_agent.market_data.freshness import assert_chain_fresh, assert_stock_snapshot_fresh
from dividend_agent.market_data.interface import (
    MarketDataAdapter,
    MarketDataError,
    require_market_data_capabilities,
)
from dividend_agent.options.chains import OptionChain
from dividend_agent.options.ex_dividend import ExDividendWindow
from dividend_agent.portfolio.lots import CoverageState
from dividend_agent.strategy.candidate_selection import (
    SelectionResult,
    select_covered_call_intents,
)


def fetch_live_option_chain(
    market_data: MarketDataAdapter,
    underlying: str,
    *,
    config: AppConfig,
    now: datetime | None = None,
) -> OptionChain:
    """Fetch and freshness-check a live chain. Fixtures are rejected."""
    require_market_data_capabilities(market_data)
    current = now or datetime.now(UTC)
    cc = config.strategy.covered_calls
    gte = current.date() + timedelta(days=max(1, cc.min_days_to_expiry - 5))
    lte = current.date() + timedelta(days=cc.max_days_to_expiry + 5)
    chain = market_data.get_option_chain(
        underlying,
        expiration_gte=gte,
        expiration_lte=lte,
        as_of=current,
    )
    if chain.source == "fixture":
        raise MarketDataError(f"live path rejected fixture chain for {underlying}")
    assert_chain_fresh(
        chain,
        max_age_seconds=config.execution.require_fresh_quote_seconds,
        now=current,
    )
    return chain


def fetch_ex_dividend_window(
    market_data: MarketDataAdapter,
    underlying: str,
    *,
    as_of: date,
) -> ExDividendWindow | None:
    require_market_data_capabilities(market_data, required=("get_dividend_calendar",))
    return market_data.next_ex_dividend_window(underlying, as_of=as_of)


def evaluate_underlying_live(
    *,
    config: AppConfig,
    market_data: MarketDataAdapter,
    coverage: CoverageState,
    account_id: str,
    now: datetime | None = None,
) -> tuple[SelectionResult, dict[str, Any]]:
    """Unattended evaluation: live chain + calendar; wall-clock time."""
    current = now or datetime.now(UTC)
    require_market_data_capabilities(market_data)
    snap = market_data.get_stock_snapshot(coverage.symbol)
    assert_stock_snapshot_fresh(
        snap,
        max_age_seconds=config.execution.require_fresh_quote_seconds,
        now=current,
    )
    chain = fetch_live_option_chain(market_data, coverage.symbol, config=config, now=current)
    ex_div = fetch_ex_dividend_window(market_data, coverage.symbol, as_of=current.date())
    result = select_covered_call_intents(
        config=config,
        chain=chain,
        coverage=coverage,
        account_id=account_id,
        as_of=current.date(),
        now=current,
        ex_div=ex_div,
    )
    meta = {
        "underlying": coverage.symbol,
        "chain_source": chain.source,
        "chain_entries": len(chain.entries),
        "ex_div": ex_div.ex_date.isoformat() if ex_div else None,
        "ex_div_source": ex_div.source if ex_div else None,
        "equity_observed_at": snap.observed_at.isoformat(),
        "evaluation_now": current.isoformat(),
        "fixture_used": False,
    }
    return result, meta
