"""Screener and memo qualification / citation validation."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from dividend_agent.agents.research_agent import DeterministicResearchAgent
from dividend_agent.config import AppConfig
from dividend_agent.research.citations import Citation, Claim
from dividend_agent.research.company_facts import extract_company_facts
from dividend_agent.research.dividends import extract_dividends_from_companyfacts
from dividend_agent.research.memo import (
    IssuerIdentity,
    build_deterministic_memo,
    validate_memo_citations,
)
from dividend_agent.research.screener import MarketSnapshotInput, screen_issuer
from dividend_agent.strategy.qualification import qualify_memo

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_screen_missing_data_not_accepted() -> None:
    cfg = AppConfig()
    from dividend_agent.research.dividends import DividendSnapshot

    result = screen_issuer(
        "GIS",
        strategy=cfg.strategy,
        dividends=DividendSnapshot(ticker="GIS", unknowns=("no_data",)),
        market=MarketSnapshotInput(),
    )
    assert result.accepted is False
    assert any(c.status == "missing" for c in result.criteria)


def test_screen_accept_with_full_market() -> None:
    cfg = AppConfig()
    raw = json.loads((FIXTURES / "sec_companyfacts_gis.json").read_text())
    div = extract_dividends_from_companyfacts(raw, ticker="GIS", cik="51462", price=Decimal("40"))
    # Force yield into 5-7% band for this unit test if needed
    if div.trailing_yield is None or not (Decimal("0.05") <= div.trailing_yield <= Decimal("0.07")):
        from dividend_agent.research.dividends import DividendLabel, DividendSnapshot

        div = DividendSnapshot(
            ticker="GIS",
            trailing_yield=Decimal("0.06"),
            trailing_yield_label=DividendLabel.TRAILING,
            price_for_yield=Decimal("40"),
        )
    result = screen_issuer(
        "GIS",
        strategy=cfg.strategy,
        dividends=div,
        market=MarketSnapshotInput(
            price=Decimal("40"),
            week52_high=Decimal("50"),
            week52_low=Decimal("30"),
            avg_dollar_volume=Decimal("50000000"),
            optionable=True,
        ),
    )
    assert result.accepted is True


def test_memo_with_known_accession_and_qualification() -> None:
    cfg = AppConfig()
    raw = json.loads((FIXTURES / "sec_companyfacts_gis.json").read_text())
    financials = extract_company_facts(raw)
    div = extract_dividends_from_companyfacts(raw, ticker="GIS", cik="51462", price=Decimal("40"))
    from dividend_agent.research.dividends import DividendLabel, DividendSnapshot

    div = DividendSnapshot(
        ticker="GIS",
        trailing_yield=Decimal("0.06"),
        trailing_yield_label=DividendLabel.TRAILING,
        price_for_yield=Decimal("40"),
        citations=div.citations,
    )
    filings = [
        Citation(
            source="sec_edgar",
            cik="0000051462",
            accession="0000051462-24-000055",
            form="10-K",
            filing_date=date(2024, 6, 26),
            url="https://www.sec.gov/Archives/edgar/data/51462/000005146224000055/",
        ),
        Citation(
            source="sec_edgar",
            cik="0000051462",
            accession="0000051462-24-000040",
            form="10-Q",
            filing_date=date(2024, 3, 20),
            url="https://www.sec.gov/Archives/edgar/data/51462/000005146224000040/",
        ),
    ]
    market = MarketSnapshotInput(
        price=Decimal("40"),
        week52_high=Decimal("50"),
        week52_low=Decimal("30"),
        avg_dollar_volume=Decimal("50000000"),
        optionable=True,
    )
    screen = screen_issuer("GIS", strategy=cfg.strategy, dividends=div, market=market)
    memo = build_deterministic_memo(
        identity=IssuerIdentity(ticker="GIS", cik="0000051462", entity_name="GENERAL MILLS INC"),
        filings=filings,
        financials=financials,
        dividends=div,
        market=market,
        screen=screen,
        max_age_days=3650,
    )
    decision, extra = qualify_memo(memo, cfg.strategy)
    assert decision in {"accept", "review"}
    assert any(c.name == "require_latest_10k" for c in extra)
    assert validate_memo_citations(memo) == []


def test_hallucinated_citation_rejected() -> None:
    agent = DeterministicResearchAgent()
    problems = agent.validate_claims(
        [
            Claim(
                statement="Made-up claim",
                citations=(Citation(source="sec_edgar", accession="not-a-real-accession"),),
            )
        ]
    )
    assert any("malformed_accession" in p for p in problems)


def test_claim_without_citations_flagged() -> None:
    agent = DeterministicResearchAgent()
    assert "missing_citations" in agent.validate_claims([Claim(statement="unsourced")])
