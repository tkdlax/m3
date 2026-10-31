"""Extract structured financial facts from SEC companyfacts JSON.

Milestone C: period classification, amended/duplicate handling, and total-debt
composition from compatible components (not tag alternatives).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dividend_agent.research.citations import Citation
from dividend_agent.research.periods import (
    PeriodKind,
    PeriodRef,
    classify_period,
    periods_compatible_for_sum,
)
from dividend_agent.research.sec_edgar import SecEdgarClient


class FinancialFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    value: Decimal
    unit: str
    end: date | None = None
    start: date | None = None
    filed: date | None = None
    form: str | None = None
    frame: str | None = None
    accession: str | None = None
    period: PeriodRef | None = None
    amended: bool = False
    citation: Citation | None = None


class CompanyFactsSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    cik: str
    entity_name: str | None = None
    facts: dict[str, FinancialFact] = Field(default_factory=dict)
    missing: tuple[str, ...] = ()
    debt_composition: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


_FACT_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ),
    "net_income": ("NetIncomeLoss",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "assets": ("Assets",),
    "common_shares_outstanding": ("CommonStockSharesOutstanding",),
}

# Total debt = sum of compatible components when present (documented rule).
_DEBT_COMPONENTS: tuple[str, ...] = (
    "LongTermDebt",
    "LongTermDebtNoncurrent",
    "LongTermDebtCurrent",
    "DebtCurrent",
    "ShortTermBorrowings",
)


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _iter_tag_facts(
    companyfacts: dict[str, Any],
    tag: str,
    *,
    cik: str,
) -> list[FinancialFact]:
    us_gaap = (companyfacts.get("facts") or {}).get("us-gaap") or {}
    if not isinstance(us_gaap, dict):
        return []
    node = us_gaap.get(tag)
    if not isinstance(node, dict):
        return []
    units = node.get("units") or {}
    series = units.get("USD") or units.get("shares") or []
    if not isinstance(series, list):
        return []
    out: list[FinancialFact] = []
    for item in series:
        if not isinstance(item, dict) or item.get("val") is None:
            continue
        end = _parse_date(item.get("end"))
        start = _parse_date(item.get("start"))
        filed = _parse_date(item.get("filed"))
        accession = str(item["accn"]) if item.get("accn") else None
        form = str(item["form"]) if item.get("form") else None
        frame = str(item["frame"]) if item.get("frame") else None
        amended = bool(form and form.upper().endswith("/A"))
        period = classify_period(end=end, start=start, frame=frame, form=form)
        unit = "USD" if "USD" in units else "shares"
        out.append(
            FinancialFact(
                name=tag,
                value=Decimal(str(item["val"])),
                unit=unit,
                end=end,
                start=start,
                filed=filed,
                form=form,
                frame=frame,
                accession=accession,
                period=period,
                amended=amended,
                citation=Citation(
                    source="sec_companyfacts",
                    cik=cik.zfill(10),
                    accession=accession,
                    form=form,
                    filing_date=filed,
                    report_period=end,
                    fact_name=tag,
                    url=(SecEdgarClient.filing_archive_url(cik, accession) if accession else None),
                ),
            )
        )
    return out


def _dedupe_amended(facts: list[FinancialFact]) -> list[FinancialFact]:
    """Keep latest filing for the same (tag, end, period kind); prefer amended when newer."""
    best: dict[tuple[str, date | None, PeriodKind], FinancialFact] = {}
    for fact in facts:
        kind = fact.period.kind if fact.period else PeriodKind.UNKNOWN
        key = (fact.name, fact.end, kind)
        prev = best.get(key)
        if prev is None:
            best[key] = fact
            continue
        prev_key = (prev.filed or date.min, 1 if prev.amended else 0)
        fact_key = (fact.filed or date.min, 1 if fact.amended else 0)
        if fact_key >= prev_key:
            best[key] = fact
    return list(best.values())


def _prefer_duration_facts(facts: list[FinancialFact]) -> list[FinancialFact]:
    """Keep annual and quarterly overlaps; prefer annual when picking 'latest'."""
    return facts


def _latest_preferred(
    facts: list[FinancialFact],
    *,
    prefer_kinds: tuple[PeriodKind, ...] = (
        PeriodKind.ANNUAL,
        PeriodKind.QUARTERLY,
        PeriodKind.INSTANT,
    ),
) -> FinancialFact | None:
    if not facts:
        return None
    ranked = sorted(
        facts,
        key=lambda f: (
            f.filed or date.min,
            f.end or date.min,
            # Prefer preferred kinds
            max(
                (
                    len(prefer_kinds) - prefer_kinds.index(f.period.kind)
                    if f.period and f.period.kind in prefer_kinds
                    else 0
                ),
                0,
            ),
        ),
        reverse=True,
    )
    return ranked[0]


def _latest_usd_fact(
    companyfacts: dict[str, Any],
    tag_candidates: tuple[str, ...],
    *,
    cik: str,
) -> FinancialFact | None:
    collected: list[FinancialFact] = []
    for tag in tag_candidates:
        collected.extend(_iter_tag_facts(companyfacts, tag, cik=cik))
    collected = _dedupe_amended(collected)
    collected = _prefer_duration_facts(collected)
    return _latest_preferred(collected)


def compose_total_debt(
    companyfacts: dict[str, Any],
    *,
    cik: str,
) -> tuple[FinancialFact | None, tuple[str, ...], tuple[str, ...]]:
    """Sum compatible debt components for the newest shared period end.

    Rules:
    - Prefer instant / balance-sheet components with the same ``end`` date.
    - Do not add annual flow tags to instant balances.
    - If only a single aggregate LongTermDebt exists, use it alone.
    - Missing components are noted; never invent zeros to force a total.
    """
    by_tag: dict[str, list[FinancialFact]] = {}
    for tag in _DEBT_COMPONENTS:
        facts = _dedupe_amended(_iter_tag_facts(companyfacts, tag, cik=cik))
        if facts:
            by_tag[tag] = facts

    notes: list[str] = []
    if not by_tag:
        return None, (), ("total_debt_components_missing",)

    # Candidate ends from LongTermDebt* / DebtCurrent
    ends: set[date] = set()
    for facts in by_tag.values():
        for f in facts:
            if (
                f.end is not None
                and f.period
                and f.period.kind
                in {
                    PeriodKind.INSTANT,
                    PeriodKind.UNKNOWN,
                    PeriodKind.ANNUAL,
                }
            ):
                ends.add(f.end)
    if not ends:
        return None, (), ("total_debt_no_period_end",)

    end = max(ends)
    chosen: list[FinancialFact] = []
    used_tags: list[str] = []
    # Prefer noncurrent + current pair when both available for same end.
    debt_tags = (
        "LongTermDebtNoncurrent",
        "LongTermDebtCurrent",
        "DebtCurrent",
        "ShortTermBorrowings",
    )
    for tag in debt_tags:
        for f in by_tag.get(tag, []):
            if (
                f.end == end
                and f.period
                and periods_compatible_for_sum(f.period, PeriodRef(kind=f.period.kind, end=end))
            ):
                chosen.append(f)
                used_tags.append(tag)
                break

    if len(chosen) >= 2:
        total = sum((f.value for f in chosen), Decimal("0"))
        cite = chosen[0].citation
        fact = FinancialFact(
            name="total_debt_composed",
            value=total,
            unit="USD",
            end=end,
            filed=max((f.filed or date.min) for f in chosen),
            form=chosen[0].form,
            period=chosen[0].period,
            citation=cite,
        )
        notes.append("total_debt_sum_of_compatible_components")
        return fact, tuple(used_tags), tuple(notes)

    # Fallback: single LongTermDebt (or LongTermDebtNoncurrent) at end — not alternatives race.
    for tag in ("LongTermDebt", "LongTermDebtNoncurrent", "DebtCurrent"):
        for f in by_tag.get(tag, []):
            if f.end == end:
                notes.append(f"total_debt_single_component:{tag}")
                return f.model_copy(update={"name": "total_debt"}), (tag,), tuple(notes)

    notes.append("total_debt_unresolved_incompatible_components")
    return None, tuple(used_tags), tuple(notes)


def extract_company_facts(
    companyfacts: dict[str, Any],
    *,
    required: tuple[str, ...] = ("revenue", "net_income", "operating_cash_flow"),
) -> CompanyFactsSnapshot:
    """Parse selected facts; list missing required keys without inventing values."""
    cik = str(companyfacts.get("cik") or "").zfill(10)
    entity = None
    entity_raw = companyfacts.get("entityName")
    if entity_raw:
        entity = str(entity_raw)
    facts: dict[str, FinancialFact] = {}
    missing: list[str] = []
    notes: list[str] = []

    for logical, tags in _FACT_TAGS.items():
        found = _latest_usd_fact(companyfacts, tags, cik=cik)
        if found is None:
            if logical in required:
                missing.append(logical)
            continue
        # Detect annual vs quarterly overlap ambiguity for revenue
        if found.period and found.period.kind == PeriodKind.UNKNOWN:
            notes.append(f"{logical}_period_unknown")
        facts[logical] = found

    debt, composition, debt_notes = compose_total_debt(companyfacts, cik=cik)
    notes.extend(debt_notes)
    if debt is not None:
        facts["total_debt"] = debt
    elif "total_debt" not in facts:
        # Not in required by default — record absence explicitly
        notes.append("total_debt_missing")

    for logical in required:
        if logical not in facts and logical not in missing:
            missing.append(logical)

    return CompanyFactsSnapshot(
        cik=cik,
        entity_name=entity,
        facts=facts,
        missing=tuple(missing),
        debt_composition=composition,
        notes=tuple(dict.fromkeys(notes)),
    )


def load_company_facts(client: SecEdgarClient, cik: str) -> CompanyFactsSnapshot:
    return extract_company_facts(client.get_company_facts(cik))
