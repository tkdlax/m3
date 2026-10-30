"""Company facts and dividend labeling tests."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from dividend_agent.research.company_facts import extract_company_facts
from dividend_agent.research.dividends import DividendLabel, extract_dividends_from_companyfacts

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_extract_facts_and_missing() -> None:
    raw = json.loads((FIXTURES / "sec_companyfacts_gis.json").read_text())
    snap = extract_company_facts(raw)
    assert "revenue" in snap.facts
    assert snap.facts["revenue"].citation is not None
    assert snap.facts["revenue"].citation.accession == "0000051462-24-000055"
    assert snap.missing == ()


def test_missing_facts_fail_closed_list() -> None:
    snap = extract_company_facts({"cik": 1, "facts": {"us-gaap": {}}})
    assert "revenue" in snap.missing
    assert "net_income" in snap.missing


def test_dividends_declared_vs_forecast_labels() -> None:
    raw = json.loads((FIXTURES / "sec_companyfacts_gis.json").read_text())
    div = extract_dividends_from_companyfacts(raw, ticker="GIS", cik="51462", price=Decimal("40"))
    assert div.events
    assert all(e.label == DividendLabel.DECLARED for e in div.events)
    assert div.trailing_yield_label == DividendLabel.TRAILING
    assert div.forward_yield_label == DividendLabel.FORECAST
    assert div.trailing_yield is not None
    assert any("forward_yield_is_annualized" in u for u in div.unknowns)


def test_dividends_without_price_no_invented_yield() -> None:
    raw = json.loads((FIXTURES / "sec_companyfacts_gis.json").read_text())
    div = extract_dividends_from_companyfacts(raw, ticker="GIS", cik="51462")
    assert div.trailing_yield is None
    assert "market_price_missing_yields_not_computed" in div.unknowns
