"""Milestone B: market-data adapter, freshness, calendar, live evaluation path."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from dividend_agent.brokers.credentials import AlpacaPaperCredentials
from dividend_agent.config import AppConfig
from dividend_agent.market_data.alpaca_rest import (
    AlpacaMarketDataRestAdapter,
    assert_data_base_url,
)
from dividend_agent.market_data.factory import (
    UnconfiguredMarketDataAdapter,
    create_market_data_adapter,
)
from dividend_agent.market_data.fake import (
    FakeMarketDataAdapter,
    fresh_gis_chain_from_fixture,
    sample_dividend,
)
from dividend_agent.market_data.freshness import (
    assert_chain_fresh,
    assert_stock_snapshot_fresh,
)
from dividend_agent.market_data.interface import (
    MARKET_DATA_FORBIDDEN_WRITES,
    MARKET_DATA_READ_CAPABILITIES,
    MarketDataAuthError,
    MarketDataCapabilityError,
    MarketDataError,
    MarketDataStaleError,
    require_market_data_capabilities,
)
from dividend_agent.market_data.occ import parse_occ_symbol
from dividend_agent.market_data.service import evaluate_underlying_live, fetch_live_option_chain
from dividend_agent.market_data.types import StockSnapshot
from dividend_agent.options.contract_spec import OptionRight
from dividend_agent.portfolio.lots import CoverageState, ShareLot

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "option_chain_gis.json"


def test_data_base_url_exact_host() -> None:
    assert assert_data_base_url("https://data.alpaca.markets/") == "https://data.alpaca.markets"
    with pytest.raises(MarketDataError):
        assert_data_base_url("https://data.example.com")
    with pytest.raises(MarketDataError):
        assert_data_base_url("https://paper-api.alpaca.markets")


def test_unconfigured_has_no_capabilities() -> None:
    md = create_market_data_adapter(environ={})
    assert isinstance(md, UnconfiguredMarketDataAdapter)
    assert md.discover_capabilities() == ()
    with pytest.raises(MarketDataCapabilityError):
        require_market_data_capabilities(md)
    with pytest.raises(MarketDataCapabilityError):
        md.get_option_chain("GIS")


def test_auth_expired_fail_closed_no_capabilities() -> None:
    md = FakeMarketDataAdapter(auth_expired=True)
    assert md.discover_capabilities() == ()
    with pytest.raises(MarketDataAuthError):
        md.get_stock_snapshot("GIS")


def test_missing_capability_blocks_evaluation(tmp_path: Path) -> None:
    chain = fresh_gis_chain_from_fixture(str(FIXTURE))
    md = FakeMarketDataAdapter(chain=chain, capabilities=("get_stock_snapshot",))
    with pytest.raises(MarketDataCapabilityError):
        require_market_data_capabilities(md)


def test_fixture_source_rejected_on_live_path() -> None:
    from dividend_agent.options.chains import load_chain_json

    chain = load_chain_json(FIXTURE)
    assert chain.source == "fixture"
    with pytest.raises(MarketDataStaleError, match="fixture"):
        assert_chain_fresh(chain, max_age_seconds=3600)


def test_stale_quote_rejected() -> None:
    chain = fresh_gis_chain_from_fixture(str(FIXTURE))
    # Force stale timestamps
    stale = datetime.now(UTC) - timedelta(hours=2)
    entries = []
    for e in chain.entries:
        q = e.quote.model_copy(update={"quote_as_of": stale})
        entries.append(e.model_copy(update={"quote": q}))
    chain = chain.model_copy(update={"entries": tuple(entries)})
    with pytest.raises(MarketDataStaleError, match="stale"):
        assert_chain_fresh(chain, max_age_seconds=60)


def test_live_evaluation_without_fixture_json() -> None:
    chain = fresh_gis_chain_from_fixture(str(FIXTURE))
    md = FakeMarketDataAdapter(
        chain=chain,
        dividends=[sample_dividend("GIS", days_ahead=120)],
        snapshot=StockSnapshot(
            symbol="GIS",
            price=Decimal("42"),
            observed_at=datetime.now(UTC),
            source="fake_market_data",
        ),
    )
    cfg = AppConfig()
    cfg = cfg.model_copy(
        update={
            "execution": cfg.execution.model_copy(
                update={
                    "min_option_bid_usd_per_share": 0.05,
                    "max_option_bid_ask_spread_pct": 0.5,
                    "min_option_open_interest": 10,
                    "require_fresh_quote_seconds": 3600,
                }
            )
        }
    )
    coverage = CoverageState(
        symbol="GIS",
        lots=(ShareLot(qty=Decimal("100"), cost_per_share=Decimal("40")),),
    )
    result, meta = evaluate_underlying_live(
        config=cfg,
        market_data=md,
        coverage=coverage,
        account_id="test",
    )
    assert meta["fixture_used"] is False
    assert meta["chain_source"] == "fake_market_data"
    assert isinstance(result.intents, tuple)


def test_forbidden_writes_not_in_capabilities() -> None:
    for name in MARKET_DATA_FORBIDDEN_WRITES:
        assert name not in MARKET_DATA_READ_CAPABILITIES


def test_parse_occ() -> None:
    parsed = parse_occ_symbol("GIS260401C00045000")
    assert parsed is not None
    assert parsed["underlying"] == "GIS"
    assert parsed["right"] == OptionRight.CALL
    assert parsed["strike"] == Decimal("45")
    assert parsed["expiration"] == date(2026, 4, 1)


def test_alpaca_rest_maps_snapshots_and_calendar() -> None:
    creds = AlpacaPaperCredentials("k", "s", "https://paper-api.alpaca.markets")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/v2/stocks/GIS/snapshot" in url:
            return httpx.Response(
                200,
                json={
                    "latestTrade": {"p": "42.1", "t": "2026-10-30T15:00:00Z"},
                    "latestQuote": {
                        "bp": "42.0",
                        "ap": "42.2",
                        "t": "2026-10-30T15:00:00Z",
                    },
                },
            )
        if "/v2/options/contracts" in url:
            return httpx.Response(
                200,
                json={
                    "option_contracts": [
                        {
                            "id": "opt1",
                            "symbol": "GIS260401C00045000",
                            "underlying_symbol": "GIS",
                            "expiration_date": "2026-04-01",
                            "strike_price": "45",
                            "type": "call",
                            "status": "active",
                            "multiplier": "100",
                        }
                    ]
                },
            )
        if "/v1beta1/options/snapshots/GIS" in url:
            return httpx.Response(
                200,
                json={
                    "snapshots": {
                        "GIS260401C00045000": {
                            "latestQuote": {
                                "bp": "0.55",
                                "ap": "0.65",
                                "t": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                            },
                            "impliedVolatility": "0.18",
                            "openInterest": 500,
                        }
                    }
                },
            )
        if "/v1/corporate-actions" in url:
            return httpx.Response(
                200,
                json={
                    "cash_dividends": [
                        {
                            "symbol": "GIS",
                            "ex_date": (date.today() + timedelta(days=40)).isoformat(),
                            "rate": "0.50",
                        },
                        {
                            "symbol": "GIS",
                            # incomplete — must be dropped
                            "rate": "0.50",
                        },
                    ]
                },
            )
        if request.url.path.endswith("/snapshot"):
            return httpx.Response(
                200,
                json={"latestTrade": {"p": "1", "t": "2026-10-30T15:00:00Z"}},
            )
        return httpx.Response(404, json={"message": url})

    transport = httpx.MockTransport(handler)
    data_client = httpx.Client(
        transport=transport, base_url="https://data.alpaca.markets", headers={}
    )
    trading_client = httpx.Client(
        transport=transport, base_url="https://paper-api.alpaca.markets", headers={}
    )
    adapter = AlpacaMarketDataRestAdapter(
        creds, data_client=data_client, trading_client=trading_client
    )
    caps = adapter.discover_capabilities()
    assert set(MARKET_DATA_READ_CAPABILITIES).issubset(set(caps))
    snap = adapter.get_stock_snapshot("GIS")
    assert snap.price == Decimal("42.1")
    assert snap.source == "alpaca_data_rest"
    chain = adapter.get_option_chain(
        "GIS", expiration_gte=date(2026, 3, 1), expiration_lte=date(2026, 5, 1)
    )
    assert chain.source == "alpaca_data_rest"
    assert len(chain.entries) >= 1
    assert chain.entries[0].quote.bid == Decimal("0.55")
    cal = adapter.get_dividend_calendar(
        ("GIS",),
        window_start=date.today(),
        window_end=date.today() + timedelta(days=90),
    )
    assert len(cal.entries) == 1
    assert cal.entries[0].ex_date > date.today()
    adapter.close()


def test_alpaca_rest_auth_failure() -> None:
    creds = AlpacaPaperCredentials("k", "s", "https://paper-api.alpaca.markets")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    transport = httpx.MockTransport(handler)
    data_client = httpx.Client(
        transport=transport, base_url="https://data.alpaca.markets", headers={}
    )
    trading_client = httpx.Client(
        transport=transport, base_url="https://paper-api.alpaca.markets", headers={}
    )
    adapter = AlpacaMarketDataRestAdapter(
        creds, data_client=data_client, trading_client=trading_client
    )
    with pytest.raises(MarketDataAuthError):
        adapter.discover_capabilities()
    adapter.close()


def test_monthly_live_path_without_chain_file(tmp_path: Path) -> None:
    from dividend_agent.brokers.fake_paper import FakePaperBroker
    from dividend_agent.brokers.types import PositionSnapshot
    from dividend_agent.scheduling.cycle import run_jobs
    from dividend_agent.storage import LedgerRepository, open_migrated

    chain = fresh_gis_chain_from_fixture(str(FIXTURE))
    md = FakeMarketDataAdapter(
        chain=chain,
        dividends=[sample_dividend("GIS", days_ahead=120)],
        snapshot=StockSnapshot(
            symbol="GIS",
            price=Decimal("42"),
            observed_at=datetime.now(UTC),
            source="fake_market_data",
        ),
    )
    broker = FakePaperBroker(
        positions=[
            PositionSnapshot(
                symbol="GIS",
                qty=Decimal("100"),
                avg_entry_price=Decimal("40"),
                market_value=Decimal("4200"),
                side="long",
            )
        ]
    )
    # Seed ledger holdings via reconcile path — monthly reads ledger holdings.
    db = tmp_path / "m.sqlite3"
    conn = open_migrated(db)
    ledger = LedgerRepository(conn)
    from dividend_agent.execution.reconciliation import Reconciler

    Reconciler(broker, ledger).reconcile()
    conn.close()

    cfg = AppConfig()
    cfg = cfg.model_copy(
        update={
            "execution": cfg.execution.model_copy(
                update={
                    "min_option_bid_usd_per_share": 0.05,
                    "max_option_bid_ask_spread_pct": 0.5,
                    "min_option_open_interest": 10,
                    "require_fresh_quote_seconds": 3600,
                }
            )
        }
    )
    cycle = run_jobs(
        config=cfg,
        broker=broker,
        db_path=db,
        reports_dir=tmp_path / "reports",
        job_names=["monthly"],
        period_keys={"monthly": "2026-10"},
        environ={
            "TRADING_PAUSED": "true",
            "ALPACA_PAPER_API_KEY": "x",
            "ALPACA_PAPER_API_SECRET": "y",
        },
        market_data=md,
        # no option_chain_path — live path
    )
    assert cycle.results[0].status == "completed"
    payload = cycle.results[0].payload
    assert payload["call_selection"]["chain_mode"] == "live_market_data"
    assert payload["call_selection"]["fixture_used"] is False


def test_stock_snapshot_freshness() -> None:
    snap = StockSnapshot(
        symbol="GIS",
        price=Decimal("40"),
        observed_at=datetime.now(UTC),
        source="t",
    )
    assert_stock_snapshot_fresh(snap, max_age_seconds=60)
    stale = snap.model_copy(update={"observed_at": datetime.now(UTC) - timedelta(hours=1)})
    with pytest.raises(MarketDataStaleError):
        assert_stock_snapshot_fresh(stale, max_age_seconds=60)


def test_fetch_live_rejects_empty_capabilities() -> None:
    md = FakeMarketDataAdapter(capabilities=())
    with pytest.raises(MarketDataCapabilityError):
        fetch_live_option_chain(md, "GIS", config=AppConfig())
