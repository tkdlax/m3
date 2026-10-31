"""Covered-call candidate selection → explainable TradeIntents (no submission)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from dividend_agent.config import AppConfig
from dividend_agent.domain.models import TradeIntent
from dividend_agent.options.chains import OptionChain
from dividend_agent.options.ex_dividend import ExDividendWindow
from dividend_agent.options.gates import CandidateEvaluation, evaluate_covered_call_candidate
from dividend_agent.portfolio.lots import CoverageState
from dividend_agent.strategy.allocation import allocate_covered_calls


class SelectionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    underlying: str
    evaluations: tuple[CandidateEvaluation, ...] = ()
    intents: tuple[TradeIntent, ...] = ()
    alternatives: tuple[CandidateEvaluation, ...] = ()
    skipped: tuple[str, ...] = ()
    allocation_notes: tuple[str, ...] = ()


def select_covered_call_intents(
    *,
    config: AppConfig,
    chain: OptionChain,
    coverage: CoverageState,
    account_id: str = "paper",
    as_of: date | None = None,
    now: datetime | None = None,
    ex_div: ExDividendWindow | None = None,
    rule_config_version: str = "strategy.schema.v1",
) -> SelectionResult:
    """Evaluate chain calls; emit TradeIntents only for allocated passing candidates.

    All accepted candidates are ranked; only those that fit remaining unencumbered
    shares become executable intents. Others are retained as ``alternatives``.
    """
    as_of = as_of or chain.as_of or date.today()
    now = now or datetime.now(UTC)
    evaluations: list[CandidateEvaluation] = []
    skipped: list[str] = []

    spot = chain.underlying_price
    for entry in chain.calls():
        evaluation = evaluate_covered_call_candidate(
            config=config,
            coverage=coverage,
            contract=entry.contract,
            quote=entry.quote,
            underlying_price=spot,
            as_of=as_of,
            now=now,
            ex_div=ex_div,
        )
        evaluations.append(evaluation)
        if not evaluation.accepted:
            skipped.append(f"{entry.contract.symbol}: " + ",".join(evaluation.rejection_reasons))

    executable, alternatives, notes = allocate_covered_calls(
        coverage=coverage,
        evaluations=evaluations,
        contracts_per_lot=config.strategy.covered_calls.contracts_per_standard_lot,
        deliverable=100,
    )

    intents: list[TradeIntent] = []
    for evaluation in executable:
        premium = evaluation.premium_per_share
        intent = TradeIntent(
            account_id=account_id,
            symbol=evaluation.contract_symbol,
            instrument_id=None,
            side="sell",
            quantity=Decimal(str(config.strategy.covered_calls.contracts_per_standard_lot)),
            order_type="limit",
            limit_price=premium,
            expiry=datetime.combine(evaluation.expiration, datetime.min.time(), tzinfo=UTC),
            strike=evaluation.strike,
            deliverable_shares=100,
            market_snapshot_at=now,
            evidence_refs=evaluation.evidence_refs,
            rule_config_version=rule_config_version,
            extras={
                "underlying": coverage.symbol,
                "chance_of_profit": (
                    str(evaluation.probability.chance_of_profit) if evaluation.probability else None
                ),
                "prob_expire_otm": (
                    str(evaluation.probability.prob_expire_otm) if evaluation.probability else None
                ),
                "metric_note": (
                    "chance_of_profit is P(S_T < K+p) risk-neutral; "
                    "not combined stock+call P&L; not early-assignment probability"
                ),
                "wac": str(evaluation.wac) if evaluation.wac is not None else None,
                "gates": [g.model_dump() for g in evaluation.gates],
                "allocation": "executable",
                "submission": "not_implemented_m3",
            },
        )
        intents.append(intent)

    for alt in alternatives:
        skipped.append(f"{alt.contract_symbol}:accepted_but_not_allocated_coverage_cap")

    return SelectionResult(
        underlying=coverage.symbol,
        evaluations=tuple(evaluations),
        intents=tuple(intents),
        alternatives=tuple(alternatives),
        skipped=tuple(skipped),
        allocation_notes=tuple(notes),
    )
