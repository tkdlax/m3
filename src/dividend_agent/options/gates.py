"""Hard gates for covered-call candidates (spec §8). Explainable reject reasons."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from dividend_agent.config import AppConfig
from dividend_agent.options.contract_spec import (
    OptionContract,
    OptionQuote,
    validate_standard_covered_call,
)
from dividend_agent.options.ex_dividend import (
    ExDividendWindow,
    evaluate_ex_dividend_policy,
)
from dividend_agent.options.probability import (
    BsInputs,
    ProbabilityModelError,
    ProbabilityResult,
    compute_short_call_probabilities,
    days_between,
)
from dividend_agent.portfolio.lots import CoverageState, contracts_coverable


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: str  # pass | reject | review | missing
    detail: str = ""


class CandidateEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    underlying: str
    contract_symbol: str
    strike: Decimal
    expiration: date
    accepted: bool
    gates: tuple[GateResult, ...] = ()
    rejection_reasons: tuple[str, ...] = ()
    probability: ProbabilityResult | None = None
    premium_per_share: Decimal | None = None
    wac: Decimal | None = None
    evidence_refs: tuple[str, ...] = ()
    extras: dict[str, str] = Field(default_factory=dict)


def evaluate_covered_call_candidate(
    *,
    config: AppConfig,
    coverage: CoverageState,
    contract: OptionContract,
    quote: OptionQuote,
    underlying_price: Decimal | None,
    as_of: date,
    now: datetime | None = None,
    ex_div: ExDividendWindow | None = None,
    risk_free_rate: Decimal = Decimal("0.05"),
    dividend_yield: Decimal = Decimal("0"),
) -> CandidateEvaluation:
    """Run hard gates; never invent favorable missing values."""
    gates: list[GateResult] = []
    reasons: list[str] = []
    evidence: list[str] = []
    cc = config.strategy.covered_calls
    exec_cfg = config.execution
    now = now or datetime.now(UTC)

    # --- contract shape ---
    contract_reasons = validate_standard_covered_call(contract)
    if contract_reasons:
        for r in contract_reasons:
            gates.append(GateResult(name="contract_spec", status="reject", detail=r))
            reasons.append(r)
    else:
        gates.append(
            GateResult(
                name="contract_spec",
                status="pass",
                detail="standard_100_share_call",
            )
        )
        evidence.append(f"contract:{contract.symbol}")

    # --- coverage / encumbrance ---
    needed = Decimal(contract.deliverable_shares) * Decimal(cc.contracts_per_standard_lot)
    unenc = coverage.unencumbered_shares
    coverable = contracts_coverable(unenc, deliverable=contract.deliverable_shares)
    if unenc < needed or coverable < cc.contracts_per_standard_lot:
        gates.append(
            GateResult(
                name="coverage",
                status="reject",
                detail=(
                    f"unencumbered={unenc} needed={needed} "
                    f"reserved_calls={coverage.shares_reserved_by_open_calls}"
                ),
            )
        )
        reasons.append("insufficient_unencumbered_shares")
    else:
        gates.append(
            GateResult(
                name="coverage",
                status="pass",
                detail=f"unencumbered={unenc} coverable_contracts={coverable}",
            )
        )

    # --- WAC strike floor (Decimal, unrounded compare) ---
    wac = coverage.weighted_average_cost
    if wac is None:
        gates.append(GateResult(name="strike_floor", status="missing", detail="wac_unavailable"))
        reasons.append("missing_wac")
    elif contract.strike < wac:
        gates.append(
            GateResult(
                name="strike_floor",
                status="reject",
                detail=f"strike={contract.strike} < wac={wac}",
            )
        )
        reasons.append("strike_below_wac")
    else:
        gates.append(
            GateResult(
                name="strike_floor",
                status="pass",
                detail=f"strike={contract.strike} >= wac={wac}",
            )
        )

    # --- OTM at entry ---
    if underlying_price is None:
        gates.append(
            GateResult(name="otm_entry", status="missing", detail="underlying_price_missing")
        )
        reasons.append("missing_underlying_price")
    elif cc.require_out_of_money_at_entry and contract.strike <= underlying_price:
        gates.append(
            GateResult(
                name="otm_entry",
                status="reject",
                detail=f"strike={contract.strike} <= spot={underlying_price}",
            )
        )
        reasons.append("not_otm_at_entry")
    else:
        gates.append(
            GateResult(
                name="otm_entry",
                status="pass",
                detail=f"strike={contract.strike} > spot={underlying_price}",
            )
        )

    # --- DTE bounds ---
    dte = days_between(as_of, contract.expiration)
    if dte < cc.min_days_to_expiry or dte > cc.max_days_to_expiry:
        gates.append(
            GateResult(
                name="dte",
                status="reject",
                detail=f"dte={dte} not in [{cc.min_days_to_expiry},{cc.max_days_to_expiry}]",
            )
        )
        reasons.append("dte_out_of_bounds")
    else:
        gates.append(GateResult(name="dte", status="pass", detail=f"dte={dte}"))

    # --- quote freshness ---
    if quote.quote_as_of is None:
        gates.append(
            GateResult(name="quote_freshness", status="missing", detail="quote_timestamp_missing")
        )
        reasons.append("missing_quote_timestamp")
    else:
        age = (now - quote.quote_as_of.astimezone(UTC)).total_seconds()
        if age > exec_cfg.require_fresh_quote_seconds:
            gates.append(
                GateResult(
                    name="quote_freshness",
                    status="reject",
                    detail=f"quote_age_seconds={age:.0f} > {exec_cfg.require_fresh_quote_seconds}",
                )
            )
            reasons.append("stale_quote")
        else:
            gates.append(
                GateResult(
                    name="quote_freshness",
                    status="pass",
                    detail=f"quote_age_seconds={age:.0f}",
                )
            )

    # --- premium / liquidity (§21 nulls fail closed — no permissive invent) ---
    premium = quote.premium_per_share_conservative
    if premium is None:
        gates.append(GateResult(name="premium", status="missing", detail="bid_missing_fail_closed"))
        reasons.append("missing_bid_premium")
    else:
        gates.append(GateResult(name="premium", status="pass", detail=f"bid={premium}"))
        evidence.append(f"premium_bid:{premium}")

    liq_reasons = _liquidity_gates(exec_cfg, quote, premium, gates)
    reasons.extend(liq_reasons)

    # --- ex-dividend ---
    ex_result = evaluate_ex_dividend_policy(
        policy=cc.ex_dividend_policy,
        as_of=as_of,
        expiration=contract.expiration,
        window=ex_div,
    )
    gates.append(GateResult(name="ex_dividend", status=ex_result.status, detail=ex_result.detail))
    if not ex_result.allowed:
        reasons.append(ex_result.detail)

    # --- probability ---
    probability: ProbabilityResult | None = None
    if (
        underlying_price is not None
        and premium is not None
        and quote.implied_volatility is not None
        and dte > 0
        and "missing_underlying_price" not in reasons
        and "missing_bid_premium" not in reasons
    ):
        try:
            probability = compute_short_call_probabilities(
                BsInputs(
                    spot=underlying_price,
                    strike=contract.strike,
                    premium_per_share=premium,
                    days_to_expiry=dte,
                    implied_volatility=quote.implied_volatility,
                    risk_free_rate=risk_free_rate,
                    dividend_yield=dividend_yield,
                )
            )
            floor = Decimal(str(cc.chance_of_profit_min))
            if not cc.aggressive_mode_enabled and probability.chance_of_profit < floor:
                # Aggressive band not auto-enabled
                if probability.chance_of_profit >= Decimal(str(cc.aggressive_probability_floor)):
                    gates.append(
                        GateResult(
                            name="chance_of_profit",
                            status="review",
                            detail=(
                                f"cop={probability.chance_of_profit} in aggressive band; "
                                "aggressive_mode_enabled=false"
                            ),
                        )
                    )
                    reasons.append("aggressive_cop_requires_approval")
                else:
                    gates.append(
                        GateResult(
                            name="chance_of_profit",
                            status="reject",
                            detail=f"cop={probability.chance_of_profit} < floor={floor}",
                        )
                    )
                    reasons.append("cop_below_floor")
            elif probability.chance_of_profit < floor:
                gates.append(
                    GateResult(
                        name="chance_of_profit",
                        status="reject",
                        detail=f"cop={probability.chance_of_profit} < floor={floor}",
                    )
                )
                reasons.append("cop_below_floor")
            else:
                gates.append(
                    GateResult(
                        name="chance_of_profit",
                        status="pass",
                        detail=f"cop={probability.chance_of_profit} >= {floor}",
                    )
                )
                evidence.append(f"cop:{probability.chance_of_profit}")
                evidence.append(f"p_otm:{probability.prob_expire_otm}")
        except ProbabilityModelError as exc:
            gates.append(GateResult(name="chance_of_profit", status="missing", detail=str(exc)))
            reasons.append("probability_inputs_invalid")
    else:
        gates.append(
            GateResult(
                name="chance_of_profit",
                status="missing",
                detail="iv_or_inputs_missing_fail_closed",
            )
        )
        if quote.implied_volatility is None:
            reasons.append("missing_iv")

    accepted = not reasons and all(g.status == "pass" for g in gates)
    return CandidateEvaluation(
        underlying=contract.underlying,
        contract_symbol=contract.symbol,
        strike=contract.strike,
        expiration=contract.expiration,
        accepted=accepted,
        gates=tuple(gates),
        rejection_reasons=tuple(dict.fromkeys(reasons)),
        probability=probability,
        premium_per_share=premium,
        wac=wac,
        evidence_refs=tuple(evidence),
        extras={"dte": str(dte), "policy_ex_div": cc.ex_dividend_policy.value},
    )


def _liquidity_gates(
    exec_cfg: object,
    quote: OptionQuote,
    premium: Decimal | None,
    gates: list[GateResult],
) -> list[str]:
    """§21 null thresholds: fail closed (do not invent permissive defaults)."""
    reasons: list[str] = []
    min_bid = getattr(exec_cfg, "min_option_bid_usd_per_share", None)
    max_spread = getattr(exec_cfg, "max_option_bid_ask_spread_pct", None)
    min_oi = getattr(exec_cfg, "min_option_open_interest", None)

    if min_bid is None:
        gates.append(
            GateResult(
                name="min_option_bid",
                status="missing",
                detail="execution.min_option_bid_usd_per_share is null (§21)",
            )
        )
        reasons.append("unresolved_guardrail:min_option_bid_usd_per_share")
    elif premium is None or premium < Decimal(str(min_bid)):
        gates.append(
            GateResult(
                name="min_option_bid",
                status="reject",
                detail=f"premium={premium} < min_bid={min_bid}",
            )
        )
        reasons.append("premium_below_min_bid")
    else:
        gates.append(GateResult(name="min_option_bid", status="pass", detail=str(premium)))

    if max_spread is None:
        gates.append(
            GateResult(
                name="bid_ask_spread",
                status="missing",
                detail="execution.max_option_bid_ask_spread_pct is null (§21)",
            )
        )
        reasons.append("unresolved_guardrail:max_option_bid_ask_spread_pct")
    else:
        spread = quote.bid_ask_spread_pct
        if spread is None:
            gates.append(
                GateResult(name="bid_ask_spread", status="missing", detail="spread_unavailable")
            )
            reasons.append("missing_spread")
        elif spread > Decimal(str(max_spread)):
            gates.append(
                GateResult(
                    name="bid_ask_spread",
                    status="reject",
                    detail=f"spread={spread} > max={max_spread}",
                )
            )
            reasons.append("spread_too_wide")
        else:
            gates.append(GateResult(name="bid_ask_spread", status="pass", detail=str(spread)))

    if min_oi is None:
        gates.append(
            GateResult(
                name="open_interest",
                status="missing",
                detail="execution.min_option_open_interest is null (§21)",
            )
        )
        reasons.append("unresolved_guardrail:min_option_open_interest")
    elif quote.open_interest is None:
        gates.append(GateResult(name="open_interest", status="missing", detail="oi_missing"))
        reasons.append("missing_open_interest")
    elif quote.open_interest < int(min_oi):
        gates.append(
            GateResult(
                name="open_interest",
                status="reject",
                detail=f"oi={quote.open_interest} < min={min_oi}",
            )
        )
        reasons.append("open_interest_too_low")
    else:
        gates.append(
            GateResult(
                name="open_interest",
                status="pass",
                detail=str(quote.open_interest),
            )
        )

    return reasons
