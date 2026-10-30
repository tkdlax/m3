"""Extract structured financial facts from SEC companyfacts JSON."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dividend_agent.research.citations import Citation
from dividend_agent.research.sec_edgar import SecEdgarClient, SecEdgarError


class FinancialFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    value: Decimal
    unit: str
    end: date | None = None
    filed: date | None = None
    form: str | None = None
    frame: str | None = None
    accession: str | None = None
    citation: Citation | None = None


class CompanyFactsSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    cik: str
    entity_name: str | None = None
    facts: dict[str, FinancialFact] = Field(default_factory=dict)
    missing: tuple[str, ...] = ()


# Prefer common US-GAAP tags; absence is recorded, never invented.
_FACT_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ),
    "net_income": ("NetIncomeLoss",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "total_debt": (
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "DebtCurrent",
    ),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "assets": ("Assets",),
    "common_shares_outstanding": ("CommonStockSharesOutstanding",),
}


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _latest_usd_fact(
    companyfacts: dict[str, Any],
    tag_candidates: tuple[str, ...],
    *,
    cik: str,
) -> FinancialFact | None:
    us_gaap = (companyfacts.get("facts") or {}).get("us-gaap") or {}
    if not isinstance(us_gaap, dict):
        return None
    best: FinancialFact | None = None
    for tag in tag_candidates:
        node = us_gaap.get(tag)
        if not isinstance(node, dict):
            continue
        units = node.get("units") or {}
        series = units.get("USD") or units.get("shares") or []
        if not isinstance(series, list):
            continue
        for item in series:
            if not isinstance(item, dict):
                continue
            raw_val = item.get("val")
            if raw_val is None:
                continue
            end = _parse_date(item.get("end"))
            filed = _parse_date(item.get("filed"))
            accession = str(item["accn"]) if item.get("accn") else None
            form = str(item["form"]) if item.get("form") else None
            unit = "USD" if "USD" in units else "shares"
            fact = FinancialFact(
                name=tag,
                value=Decimal(str(raw_val)),
                unit=unit,
                end=end,
                filed=filed,
                form=form,
                frame=str(item["frame"]) if item.get("frame") else None,
                accession=accession,
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
            if best is None:
                best = fact
                continue
            best_key = (best.filed or date.min, best.end or date.min)
            fact_key = (fact.filed or date.min, fact.end or date.min)
            if fact_key > best_key:
                best = fact
    return best


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
    for logical, tags in _FACT_TAGS.items():
        found = _latest_usd_fact(companyfacts, tags, cik=cik)
        if found is None:
            if logical in required:
                missing.append(logical)
            continue
        facts[logical] = found
    for logical in required:
        if logical not in facts and logical not in missing:
            missing.append(logical)
    return CompanyFactsSnapshot(
        cik=cik,
        entity_name=entity,
        facts=facts,
        missing=tuple(missing),
    )


def load_company_facts_snapshot(client: SecEdgarClient, cik: str) -> CompanyFactsSnapshot:
    raw = client.get_company_facts(cik)
    snap = extract_company_facts(raw)
    if not snap.cik or snap.cik == "0000000000":
        # companyfacts payload may omit cik; use request cik
        return CompanyFactsSnapshot(
            cik=str(cik).zfill(10),
            entity_name=snap.entity_name,
            facts=snap.facts,
            missing=snap.missing,
        )
    return snap


def require_no_missing_critical(snapshot: CompanyFactsSnapshot) -> None:
    if snapshot.missing:
        raise SecEdgarError(
            "missing critical company facts (fail closed): " + ", ".join(snapshot.missing)
        )
