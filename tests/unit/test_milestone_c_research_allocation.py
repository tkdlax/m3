"""Milestone C acceptance: normalization, narrative, cache, allocation overcommit."""

from __future__ import annotations

import json
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

from dividend_agent.agents.research_agent import LlmResearchAgentStub, create_research_agent
from dividend_agent.agents.schema import (
    ResearchBudget,
    StructuredResearchRequest,
    validate_structured_response,
)
from dividend_agent.config import AppConfig
from dividend_agent.options.gates import CandidateEvaluation, GateResult
from dividend_agent.options.probability import ProbabilityResult
from dividend_agent.portfolio.lots import CoverageState, ShareLot
from dividend_agent.research.citations import Citation, Claim
from dividend_agent.research.company_facts import compose_total_debt, extract_company_facts
from dividend_agent.research.dividends import (
    DividendEvent,
    DividendLabel,
    extract_dividends_from_companyfacts,
    normalize_dividend_events,
)
from dividend_agent.research.memo import IssuerIdentity, ResearchMemo
from dividend_agent.research.narrative import analyze_filing_narrative
from dividend_agent.research.periods import PeriodKind, classify_period, periods_compatible_for_sum
from dividend_agent.research.pipeline import ResearchPipeline
from dividend_agent.research.screener import MarketSnapshotInput
from dividend_agent.research.sec_edgar import SecEdgarClient
from dividend_agent.strategy.allocation import allocate_covered_calls, rank_covered_call_candidates
from dividend_agent.strategy.qualification import qualify_memo

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _citation(**kwargs: object) -> Citation:
    base = {
        "source": "sec_edgar",
        "cik": "0000051462",
        "accession": "0000051462-24-000055",
        "form": "10-K",
        "filing_date": date(2024, 6, 26),
        "url": "https://www.sec.gov/Archives/edgar/data/51462/000005146224000055/gis.htm",
    }
    base.update(kwargs)
    return Citation(**base)  # type: ignore[arg-type]


def _sec_client_with_optional_filing(
    *,
    filing_text: str | None = None,
    cache_dir: Path | None = None,
    cache_ttl_seconds: int = 86400,
) -> SecEdgarClient:
    tickers = json.loads((FIXTURES / "sec_company_tickers.json").read_text())
    submissions = json.loads((FIXTURES / "sec_submissions_gis.json").read_text())
    facts = json.loads((FIXTURES / "sec_companyfacts_gis.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/files/company_tickers.json"):
            return httpx.Response(200, json=tickers)
        if "submissions/CIK0000051462.json" in url:
            return httpx.Response(200, json=submissions)
        if "companyfacts/CIK0000051462.json" in url:
            return httpx.Response(200, json=facts)
        if filing_text is not None and ("Archives/edgar" in url or url.endswith(".htm")):
            return httpx.Response(200, text=filing_text)
        return httpx.Response(404)

    return SecEdgarClient(
        "DividendAgent/0.1 (tests@example.com)",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
    )


_NARRATIVE_TEXT = (
    "Item 1. Business. We manufacture and market branded consumer foods worldwide. "
    "Our business includes ready-to-eat cereals and snacks across retail channels. "
    + ("x" * 200)
    + " Item 1A. Risk Factors. Competition, commodity costs, and consumer preferences "
    "may adversely affect results of operations. "
    + ("y" * 200)
    + " Long-term debt and credit facility borrowings support liquidity. "
    "Cash flows from operating activities fund dividends. "
    "Dividends declared under our dividend policy totaled amounts disclosed herein."
)


# --- Periods / duplicate filings / overlaps ---


def test_classify_annual_vs_quarterly_frames() -> None:
    annual = classify_period(end=date(2024, 5, 26), frame="CY2024")
    q = classify_period(end=date(2024, 2, 25), frame="CY2024Q3")
    ytd = classify_period(end=date(2024, 2, 25), frame="CY2024Q3YTD")
    instant = classify_period(end=date(2024, 5, 26), frame="CY2024I")
    assert annual.kind == PeriodKind.ANNUAL
    assert q.kind == PeriodKind.QUARTERLY
    assert ytd.kind == PeriodKind.YEAR_TO_DATE
    assert instant.kind == PeriodKind.INSTANT
    assert not periods_compatible_for_sum(annual, q)


def test_duplicate_amended_facts_prefer_newer() -> None:
    raw = {
        "cik": 51462,
        "entityName": "TEST",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "val": 100,
                                "end": "2023-05-28",
                                "start": "2022-05-30",
                                "filed": "2023-06-01",
                                "form": "10-K",
                                "frame": "CY2023",
                                "accn": "0000051462-23-000001",
                            },
                            {
                                "val": 110,
                                "end": "2023-05-28",
                                "start": "2022-05-30",
                                "filed": "2023-07-01",
                                "form": "10-K/A",
                                "frame": "CY2023",
                                "accn": "0000051462-23-000099",
                            },
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "val": 10,
                                "end": "2023-05-28",
                                "start": "2022-05-30",
                                "filed": "2023-07-01",
                                "form": "10-K/A",
                                "frame": "CY2023",
                                "accn": "0000051462-23-000099",
                            }
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {
                                "val": 20,
                                "end": "2023-05-28",
                                "start": "2022-05-30",
                                "filed": "2023-07-01",
                                "form": "10-K/A",
                                "frame": "CY2023",
                                "accn": "0000051462-23-000099",
                            }
                        ]
                    }
                },
            }
        },
    }
    snap = extract_company_facts(raw)
    assert snap.facts["revenue"].value == Decimal("110")
    assert snap.facts["revenue"].amended is True


def test_annual_quarterly_overlap_not_summed_as_same_kind() -> None:
    a = classify_period(end=date(2024, 5, 26), frame="CY2024")
    q = classify_period(end=date(2024, 5, 26), frame="CY2024Q4")
    assert a.kind != q.kind
    assert not periods_compatible_for_sum(a, q)


def test_total_debt_composes_compatible_components() -> None:
    raw = {
        "cik": 51462,
        "facts": {
            "us-gaap": {
                "LongTermDebtNoncurrent": {
                    "units": {
                        "USD": [
                            {
                                "val": 800,
                                "end": "2024-05-26",
                                "filed": "2024-06-26",
                                "form": "10-K",
                                "frame": "CY2024I",
                                "accn": "0000051462-24-000055",
                            }
                        ]
                    }
                },
                "LongTermDebtCurrent": {
                    "units": {
                        "USD": [
                            {
                                "val": 200,
                                "end": "2024-05-26",
                                "filed": "2024-06-26",
                                "form": "10-K",
                                "frame": "CY2024I",
                                "accn": "0000051462-24-000055",
                            }
                        ]
                    }
                },
            }
        },
    }
    debt, composition, notes = compose_total_debt(raw, cik="51462")
    assert debt is not None
    assert debt.value == Decimal("1000")
    assert "LongTermDebtNoncurrent" in composition
    assert "LongTermDebtCurrent" in composition
    assert any("sum_of_compatible" in n for n in notes)


# --- Dividends: specials, overlaps, XBRL end ≠ ex-date ---


def test_normalize_dedupes_overlapping_periods_and_flags_specials() -> None:
    events = [
        DividendEvent(
            amount=Decimal("0.50"),
            label=DividendLabel.DECLARED,
            period_end=date(2024, 2, 25),
            declaration_date=date(2024, 3, 1),
            ex_date=date(2024, 2, 25),  # wrongly equated to period end
        ),
        DividendEvent(
            amount=Decimal("0.50"),
            label=DividendLabel.DECLARED,
            period_end=date(2024, 2, 25),
            declaration_date=date(2024, 3, 15),  # newer duplicate
        ),
        DividendEvent(
            amount=Decimal("0.51"),
            label=DividendLabel.DECLARED,
            period_end=date(2023, 11, 26),
            declaration_date=date(2023, 12, 1),
        ),
        DividendEvent(
            amount=Decimal("0.49"),
            label=DividendLabel.DECLARED,
            period_end=date(2023, 8, 27),
            declaration_date=date(2023, 9, 1),
        ),
        DividendEvent(
            amount=Decimal("0.48"),
            label=DividendLabel.DECLARED,
            period_end=date(2023, 5, 28),
            declaration_date=date(2023, 6, 1),
        ),
        DividendEvent(
            amount=Decimal("5.00"),
            label=DividendLabel.DECLARED,
            period_end=date(2023, 2, 26),
            declaration_date=date(2023, 3, 1),
        ),
    ]
    out = normalize_dividend_events(events)
    # Duplicate 0.50@2024-02-25 collapsed to one; ex_date cleared when == period_end
    feb = [e for e in out if e.period_end == date(2024, 2, 25)]
    assert len(feb) == 1
    assert feb[0].ex_date is None
    assert feb[0].declaration_date == date(2024, 3, 15)
    specials = [e for e in out if e.label == DividendLabel.SPECIAL]
    assert len(specials) == 1
    assert specials[0].amount == Decimal("5.00")


def test_special_dividends_excluded_from_ttm() -> None:
    raw = {
        "cik": 51462,
        "facts": {
            "us-gaap": {
                "CommonStockDividendsPerShareDeclared": {
                    "units": {
                        "USD/shares": [
                            {
                                "val": 0.5,
                                "end": "2024-02-25",
                                "start": "2023-11-27",
                                "filed": "2024-03-20",
                                "form": "10-Q",
                                "frame": "CY2024Q3",
                                "accn": "a",
                            },
                            {
                                "val": 0.5,
                                "end": "2023-11-26",
                                "start": "2023-08-28",
                                "filed": "2023-12-20",
                                "form": "10-Q",
                                "frame": "CY2024Q2",
                                "accn": "b",
                            },
                            {
                                "val": 0.5,
                                "end": "2023-08-27",
                                "start": "2023-05-29",
                                "filed": "2023-09-20",
                                "form": "10-Q",
                                "frame": "CY2024Q1",
                                "accn": "c",
                            },
                            {
                                "val": 0.5,
                                "end": "2023-05-28",
                                "start": "2023-02-27",
                                "filed": "2023-06-20",
                                "form": "10-Q",
                                "frame": "CY2023Q4",
                                "accn": "d",
                            },
                            {
                                "val": 4.0,
                                "end": "2023-02-26",
                                "start": "2022-11-28",
                                "filed": "2023-03-20",
                                "form": "10-Q",
                                "frame": "CY2023Q3",
                                "accn": "e",
                            },
                        ]
                    }
                }
            }
        },
    }
    div = extract_dividends_from_companyfacts(raw, ticker="GIS", cik="51462", price=Decimal("40"))
    assert any(e.label == DividendLabel.SPECIAL for e in div.events)
    assert any("special_dividends_excluded" in u for u in div.unknowns)
    assert any("xbrl_period_end_is_not_ex_or_payment_date" in u for u in div.unknowns)
    # TTM from four quarterly declared (special excluded from quarterly list)
    assert div.trailing_twelve_month == Decimal("2.0")


# --- Narrative / qualification ---


def test_link_alone_does_not_qualify() -> None:
    memo = ResearchMemo(
        identity=IssuerIdentity(ticker="GIS", cik="0000051462"),
        decision="accept",
        filings=(_citation(),),
        criteria=(),
    )
    decision, extra = qualify_memo(
        memo, AppConfig().strategy, require_narrative=True, narrative=None
    )
    assert decision == "reject"
    assert any(c.name == "filing_narrative" and c.status == "fail" for c in extra)


def test_empty_narrative_fails_closed() -> None:
    cite = _citation()
    narrative = analyze_filing_narrative("", citation=cite)
    assert narrative.has_substantive_evidence is False
    memo = ResearchMemo(
        identity=IssuerIdentity(ticker="GIS", cik="0000051462"),
        decision="accept",
        filings=(cite,),
        criteria=(),
    )
    decision, _ = qualify_memo(
        memo, AppConfig().strategy, require_narrative=True, narrative=narrative
    )
    assert decision == "reject"


def test_substantive_narrative_passes_gate() -> None:
    cite = _citation()
    ten_q = _citation(
        accession="0000051462-24-000040",
        form="10-Q",
        filing_date=date(2024, 3, 20),
    )
    narrative = analyze_filing_narrative(_NARRATIVE_TEXT, citation=cite)
    assert narrative.has_substantive_evidence is True
    assert narrative.business is not None
    assert narrative.risks is not None
    memo = ResearchMemo(
        identity=IssuerIdentity(ticker="GIS", cik="0000051462"),
        decision="accept",
        filings=(cite, ten_q),
        criteria=(),
    )
    decision, extra = qualify_memo(
        memo, AppConfig().strategy, require_narrative=True, narrative=narrative
    )
    assert decision == "accept"
    assert any(c.name == "filing_narrative" and c.status == "pass" for c in extra)


def test_pipeline_rejects_without_narrative_body() -> None:
    cfg = AppConfig().model_copy(
        update={
            "research": AppConfig().research.model_copy(
                update={
                    "sec_user_agent": "DividendAgent/0.1 (tests@example.com)",
                    "require_filing_narrative": True,
                }
            )
        }
    )
    pipeline = ResearchPipeline.from_config(cfg, sec_client=_sec_client_with_optional_filing())
    memo = pipeline.research_symbol(
        "GIS",
        market=MarketSnapshotInput(
            price=Decimal("40"),
            week52_high=Decimal("50"),
            week52_low=Decimal("30"),
            avg_dollar_volume=Decimal("50000000"),
            optionable=True,
        ),
        filing_text_overrides={"0000051462-24-000055": ""},
    )
    assert memo.decision == "reject"
    assert "missing_filing_narrative_evidence" in memo.unknowns or any(
        "narrative" in r.lower() for r in memo.decision_reasons
    )


def test_pipeline_accepts_with_narrative_override() -> None:
    cfg = AppConfig().model_copy(
        update={
            "research": AppConfig().research.model_copy(
                update={
                    "sec_user_agent": "DividendAgent/0.1 (tests@example.com)",
                    "require_filing_narrative": True,
                }
            )
        }
    )
    pipeline = ResearchPipeline.from_config(cfg, sec_client=_sec_client_with_optional_filing())
    memo = pipeline.research_symbol(
        "GIS",
        market=MarketSnapshotInput(
            price=Decimal("40"),
            week52_high=Decimal("50"),
            week52_low=Decimal("30"),
            avg_dollar_volume=Decimal("50000000"),
            optionable=True,
        ),
        filing_text_overrides={"0000051462-24-000055": _NARRATIVE_TEXT},
    )
    assert memo.extras.get("narrative", {}).get("has_substantive_evidence") is True
    # May still review/reject on screen criteria; narrative gate itself must pass
    assert not any(
        "link alone" in r.lower() or "missing substantive narrative" in r.lower()
        for r in memo.decision_reasons
    )


# --- Cache freshness ---


def test_stale_cache_refetched(tmp_path: Path) -> None:
    hits = {"n": 0}
    tickers = json.loads((FIXTURES / "sec_company_tickers.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(200, json=tickers)

    client = SecEdgarClient(
        "DividendAgent/0.1 (tests@example.com)",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache_dir=tmp_path,
        cache_ttl_seconds=1,
        min_interval_seconds=0,
    )
    client.load_ticker_map()
    assert hits["n"] == 1
    client.load_ticker_map()
    assert hits["n"] == 1  # fresh cache
    # Expire
    cache_files = list(tmp_path.glob("*.json"))
    assert cache_files
    old = time.time() - 10
    for path in cache_files:
        import os

        os.utime(path, (old, old))
    client.load_ticker_map()
    assert hits["n"] == 2


def test_zero_ttl_never_serves_cache(tmp_path: Path) -> None:
    hits = {"n": 0}
    tickers = json.loads((FIXTURES / "sec_company_tickers.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(200, json=tickers)

    client = SecEdgarClient(
        "DividendAgent/0.1 (tests@example.com)",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache_dir=tmp_path,
        cache_ttl_seconds=0,
        min_interval_seconds=0,
    )
    client.load_ticker_map()
    client.load_ticker_map()
    assert hits["n"] == 2


# --- AI adapter schema ---


def test_llm_stub_structured_schema_no_order_creds() -> None:
    agent = create_research_agent(provider="llm_stub", enabled=True, budget=ResearchBudget())
    memo = ResearchMemo(
        identity=IssuerIdentity(ticker="GIS", cik="0000051462"),
        decision="review",
    )
    req = StructuredResearchRequest(
        memo=memo,
        source_excerpts=("Item 1 Business excerpt", "api_key=secret-should-redact"),
        budget=ResearchBudget(timeout_seconds=5, max_tokens=100),
    )
    assert req.source_excerpts[1] == "[redacted]"
    resp = agent.run_structured(req)  # type: ignore[attr-defined]
    assert resp.provider == "llm_stub"
    assert resp.status == "failed"
    assert "llm_provider_not_configured" in resp.unknowns
    assert "no_invented_claims" in resp.unknowns
    problems = validate_structured_response(resp)
    assert problems == []


def test_llm_stub_enrich_does_not_invent_claims() -> None:
    stub = LlmResearchAgentStub(enabled=True)
    memo = ResearchMemo(
        identity=IssuerIdentity(ticker="GIS", cik="0000051462"),
        decision="review",
        claims=(
            Claim(
                statement="has cite",
                citations=(_citation(),),
            ),
        ),
    )
    out = stub.enrich_memo(memo, source_excerpts=["submit_order now"])
    assert out.claims == memo.claims
    assert "llm_provider_not_configured" in out.unknowns
    assert out.extras.get("structured_research") is not None


# --- Allocation without overcommit ---


def _eval(
    symbol: str,
    strike: str,
    *,
    cop: str = "0.95",
    premium: str = "0.50",
    accepted: bool = True,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        underlying="GIS",
        contract_symbol=symbol,
        strike=Decimal(strike),
        expiration=date(2026, 4, 1),
        accepted=accepted,
        gates=(GateResult(name="ok", status="pass"),),
        probability=ProbabilityResult(
            chance_of_profit=Decimal(cop),
            prob_expire_otm=Decimal("0.92"),
            breakeven=Decimal(strike) + Decimal(premium),
            premium_per_share=Decimal(premium),
            premium_per_contract=Decimal(premium) * 100,
        ),
        premium_per_share=Decimal(premium),
        wac=Decimal("40"),
    )


def test_rank_prefers_higher_cop_then_premium() -> None:
    ranked = rank_covered_call_candidates(
        [
            _eval("A", "46", cop="0.91", premium="0.80"),
            _eval("B", "47", cop="0.96", premium="0.40"),
            _eval("C", "48", cop="0.96", premium="0.70"),
            _eval("D", "45", accepted=False),
        ]
    )
    assert [e.contract_symbol for e in ranked] == ["C", "B", "A"]


def test_allocate_does_not_overcommit_shares() -> None:
    coverage = CoverageState(
        symbol="GIS",
        lots=(ShareLot(qty=Decimal("100"), cost_per_share=Decimal("40")),),
    )
    executable, alternatives, notes = allocate_covered_calls(
        coverage=coverage,
        evaluations=[
            _eval("GIS260401C00047000", "47", cop="0.97", premium="0.40"),
            _eval("GIS260401C00046000", "46", cop="0.95", premium="0.55"),
            _eval("GIS260401C00045000", "45", cop="0.93", premium="0.70"),
        ],
    )
    assert len(executable) == 1
    assert executable[0].contract_symbol == "GIS260401C00047000"
    assert len(alternatives) == 2
    assert any("coverage_capped" in n for n in notes)


def test_allocate_zero_unencumbered() -> None:
    coverage = CoverageState(
        symbol="GIS",
        lots=(ShareLot(qty=Decimal("100"), cost_per_share=Decimal("40")),),
        shares_reserved_by_open_calls=Decimal("100"),
    )
    executable, alternatives, notes = allocate_covered_calls(
        coverage=coverage,
        evaluations=[_eval("X", "45")],
    )
    assert executable == []
    assert len(alternatives) == 1
    assert "no_unencumbered_shares_for_allocation" in notes


def test_two_lots_can_allocate_two_contracts() -> None:
    coverage = CoverageState(
        symbol="GIS",
        lots=(
            ShareLot(qty=Decimal("100"), cost_per_share=Decimal("40")),
            ShareLot(qty=Decimal("100"), cost_per_share=Decimal("41")),
        ),
    )
    executable, alternatives, _ = allocate_covered_calls(
        coverage=coverage,
        evaluations=[
            _eval("A", "47", cop="0.97"),
            _eval("B", "46", cop="0.95"),
            _eval("C", "45", cop="0.93"),
        ],
    )
    assert len(executable) == 2
    assert len(alternatives) == 1
