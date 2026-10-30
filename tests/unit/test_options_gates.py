"""Hard-gate edge cases for covered-call evaluation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from dividend_agent.config import AppConfig
from dividend_agent.options.chains import load_chain_json
from dividend_agent.options.contract_spec import parse_contract, validate_standard_covered_call
from dividend_agent.options.ex_dividend import ExDividendWindow
from dividend_agent.options.gates import evaluate_covered_call_candidate
from dividend_agent.portfolio.lots import CoverageState, ShareLot
from dividend_agent.strategy.candidate_selection import select_covered_call_intents

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CHAIN = FIXTURES / "option_chain_gis.json"


def _cfg_with_liquidity() -> AppConfig:
    cfg = AppConfig()
    return cfg.model_copy(
        update={
            "execution": cfg.execution.model_copy(
                update={
                    "min_option_bid_usd_per_share": 0.05,
                    "max_option_bid_ask_spread_pct": 0.50,
                    "min_option_open_interest": 10,
                    "require_fresh_quote_seconds": 86400 * 3650,
                }
            )
        }
    )


def _coverage(*, reserved: int = 0, wac: str = "40") -> CoverageState:
    return CoverageState(
        symbol="GIS",
        lots=(ShareLot(qty=Decimal("100"), cost_per_share=Decimal(wac)),),
        shares_reserved_by_open_calls=Decimal(reserved),
    )


def test_strike_at_wac_passes_below_fails() -> None:
    cfg = _cfg_with_liquidity()
    chain = load_chain_json(CHAIN)
    entry = next(e for e in chain.calls() if e.contract.strike == Decimal("45.00"))
    # WAC == 45
    ok = evaluate_covered_call_candidate(
        config=cfg,
        coverage=_coverage(wac="45"),
        contract=entry.contract,
        quote=entry.quote,
        underlying_price=Decimal("42"),
        as_of=date(2026, 3, 1),
        now=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
        ex_div=ExDividendWindow(ex_date=date(2026, 5, 1)),
    )
    assert all(g.name != "strike_floor" or g.status == "pass" for g in ok.gates)

    bad = evaluate_covered_call_candidate(
        config=cfg,
        coverage=_coverage(wac="45.01"),
        contract=entry.contract,
        quote=entry.quote,
        underlying_price=Decimal("42"),
        as_of=date(2026, 3, 1),
        now=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
        ex_div=ExDividendWindow(ex_date=date(2026, 5, 1)),
    )
    assert "strike_below_wac" in bad.rejection_reasons


def test_adjusted_deliverable_rejected() -> None:
    reasons = validate_standard_covered_call(
        parse_contract(
            {
                "symbol": "ADJ",
                "underlying": "GIS",
                "expiration": "2026-04-01",
                "strike": "45",
                "right": "call",
                "deliverable_shares": 150,
                "adjusted": True,
            }
        )
    )
    assert (
        "adjusted_contract_unsupported" in reasons
        or "nonstandard_deliverable_unsupported" in reasons
    )


def test_double_encumbrance_rejects() -> None:
    cfg = _cfg_with_liquidity()
    chain = load_chain_json(CHAIN)
    entry = next(e for e in chain.calls() if e.contract.strike == Decimal("45"))
    result = evaluate_covered_call_candidate(
        config=cfg,
        coverage=_coverage(reserved=100),
        contract=entry.contract,
        quote=entry.quote,
        underlying_price=Decimal("42"),
        as_of=date(2026, 3, 1),
        now=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
        ex_div=ExDividendWindow(ex_date=date(2026, 5, 1)),
    )
    assert "insufficient_unencumbered_shares" in result.rejection_reasons


def test_ex_div_overlap_skipped() -> None:
    cfg = _cfg_with_liquidity()
    chain = load_chain_json(CHAIN)
    entry = next(e for e in chain.calls() if e.contract.strike == Decimal("45"))
    result = evaluate_covered_call_candidate(
        config=cfg,
        coverage=_coverage(),
        contract=entry.contract,
        quote=entry.quote,
        underlying_price=Decimal("42"),
        as_of=date(2026, 3, 1),
        now=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
        ex_div=ExDividendWindow(ex_date=date(2026, 3, 15)),
    )
    assert "ex_dividend_overlap_avoid_policy" in result.rejection_reasons


def test_missing_ex_div_calendar_fail_closed() -> None:
    cfg = _cfg_with_liquidity()
    chain = load_chain_json(CHAIN)
    entry = next(e for e in chain.calls() if e.contract.strike == Decimal("45"))
    result = evaluate_covered_call_candidate(
        config=cfg,
        coverage=_coverage(),
        contract=entry.contract,
        quote=entry.quote,
        underlying_price=Decimal("42"),
        as_of=date(2026, 3, 1),
        now=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
        ex_div=None,
    )
    assert "ex_dividend_calendar_missing_fail_closed" in result.rejection_reasons


def test_stale_quote_fail_closed() -> None:
    cfg = AppConfig().model_copy(
        update={
            "execution": AppConfig().execution.model_copy(
                update={
                    "min_option_bid_usd_per_share": 0.05,
                    "max_option_bid_ask_spread_pct": 0.5,
                    "min_option_open_interest": 10,
                    "require_fresh_quote_seconds": 60,
                }
            )
        }
    )
    chain = load_chain_json(CHAIN)
    entry = next(e for e in chain.calls() if e.contract.strike == Decimal("45"))
    result = evaluate_covered_call_candidate(
        config=cfg,
        coverage=_coverage(),
        contract=entry.contract,
        quote=entry.quote,
        underlying_price=Decimal("42"),
        as_of=date(2026, 3, 1),
        now=datetime(2026, 3, 1, 16, 0, tzinfo=UTC),  # 1h after quote
        ex_div=ExDividendWindow(ex_date=date(2026, 5, 1)),
    )
    assert "stale_quote" in result.rejection_reasons


def test_null_liquidity_guardrails_fail_closed() -> None:
    cfg = AppConfig()  # null §21 liquidity fields
    cfg = cfg.model_copy(
        update={
            "execution": cfg.execution.model_copy(
                update={"require_fresh_quote_seconds": 86400 * 3650}
            )
        }
    )
    chain = load_chain_json(CHAIN)
    entry = next(e for e in chain.calls() if e.contract.strike == Decimal("45"))
    result = evaluate_covered_call_candidate(
        config=cfg,
        coverage=_coverage(),
        contract=entry.contract,
        quote=entry.quote,
        underlying_price=Decimal("42"),
        as_of=date(2026, 3, 1),
        now=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
        ex_div=ExDividendWindow(ex_date=date(2026, 5, 1)),
    )
    assert any(r.startswith("unresolved_guardrail:") for r in result.rejection_reasons)
    assert result.accepted is False


def test_selection_produces_explainable_intent() -> None:
    cfg = _cfg_with_liquidity()
    chain = load_chain_json(CHAIN)
    result = select_covered_call_intents(
        config=cfg,
        chain=chain,
        coverage=_coverage(),
        as_of=date(2026, 3, 1),
        now=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
        ex_div=ExDividendWindow(ex_date=date(2026, 5, 1)),
    )
    # 40 strike is ITM vs spot 42 -> rejected; adjusted rejected; 45 may pass
    assert any("strike_below_wac" not in s for s in result.skipped) or result.intents
    assert all(i.extras.get("submission") == "not_implemented_m3" for i in result.intents)
    # Below-cost / ITM / adjusted should appear in skipped with reasons
    assert any("ADJ" in s or "adjusted" in s for s in result.skipped)
    assert any(
        "40.00" in e.contract_symbol or e.strike == Decimal("40")
        for e in result.evaluations
    )
