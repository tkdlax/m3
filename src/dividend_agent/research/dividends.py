"""Dividend history and yield labeling (declared vs forecast)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dividend_agent.research.citations import Citation
from dividend_agent.research.sec_edgar import SecEdgarClient


class DividendLabel(StrEnum):
    DECLARED = "declared"
    FORECAST = "forecast"
    TRAILING = "trailing"
    UNKNOWN = "unknown"


class DividendEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: Decimal
    label: DividendLabel
    ex_date: date | None = None
    payment_date: date | None = None
    declaration_date: date | None = None
    frequency_hint: str | None = None  # quarterly | annual | unknown
    citation: Citation | None = None


class DividendSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    events: tuple[DividendEvent, ...] = ()
    trailing_twelve_month: Decimal | None = None
    trailing_yield: Decimal | None = None
    trailing_yield_label: DividendLabel = DividendLabel.TRAILING
    forward_yield: Decimal | None = None
    forward_yield_label: DividendLabel = DividendLabel.FORECAST
    annualized_from_latest_declared: Decimal | None = None
    price_for_yield: Decimal | None = None
    price_as_of: date | None = None
    unknowns: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = Field(default_factory=tuple)


def extract_dividends_from_companyfacts(
    companyfacts: dict[str, Any],
    *,
    ticker: str,
    cik: str,
    price: Decimal | None = None,
    price_as_of: date | None = None,
) -> DividendSnapshot:
    """Pull common stock cash dividends from us-gaap when present.

    Yields are only computed when price is supplied by an authorized market feed
    (not invented here). SEC facts alone do not provide current yield.
    """
    us_gaap = (companyfacts.get("facts") or {}).get("us-gaap") or {}
    tags = (
        "CommonStockDividendsPerShareDeclared",
        "PaymentsOfDividendsCommonStock",
    )
    events: list[DividendEvent] = []
    citations: list[Citation] = []
    unknowns: list[str] = []

    for tag in tags:
        node = us_gaap.get(tag)
        if not isinstance(node, dict):
            continue
        units = node.get("units") or {}
        # Prefer per-share USD series
        series = units.get("USD/shares") or units.get("USD") or []
        if not isinstance(series, list):
            continue
        for item in series:
            if not isinstance(item, dict) or item.get("val") is None:
                continue
            amount = Decimal(str(item["val"]))
            # PaymentsOfDividendsCommonStock is aggregate cash — skip for per-share events
            if tag == "PaymentsOfDividendsCommonStock" and "USD/shares" not in units:
                continue
            end = _parse_date(item.get("end"))
            filed = _parse_date(item.get("filed"))
            accession = str(item["accn"]) if item.get("accn") else None
            form = str(item["form"]) if item.get("form") else None
            citation = Citation(
                source="sec_companyfacts",
                ticker=ticker.upper(),
                cik=str(cik).zfill(10),
                accession=accession,
                form=form,
                filing_date=filed,
                report_period=end,
                fact_name=tag,
                url=(SecEdgarClient.filing_archive_url(cik, accession) if accession else None),
            )
            events.append(
                DividendEvent(
                    amount=amount,
                    label=DividendLabel.DECLARED,
                    ex_date=None,  # companyfacts rarely carry ex-date
                    payment_date=end,
                    frequency_hint="unknown",
                    citation=citation,
                )
            )
            citations.append(citation)

    if not events:
        unknowns.append("no_declared_dividend_facts_in_companyfacts")

    # Trailing estimate from last 4 declared per-share amounts when available.
    declared = sorted(
        (e for e in events if e.label == DividendLabel.DECLARED),
        key=lambda e: e.payment_date or date.min,
        reverse=True,
    )
    trailing: Decimal | None = None
    annualized: Decimal | None = None
    if declared:
        last_four = declared[:4]
        trailing = sum((e.amount for e in last_four), Decimal("0"))
        annualized = declared[0].amount * Decimal("4")
        if len(last_four) < 4:
            unknowns.append("trailing_ttm_incomplete_fewer_than_4_declared_events")

    trailing_yield = None
    forward_yield = None
    if price is None or price <= 0:
        unknowns.append("market_price_missing_yields_not_computed")
    else:
        if trailing is not None:
            trailing_yield = trailing / price
        if annualized is not None:
            # Labeled FORECAST — annualized from latest declared, not a company guidance.
            forward_yield = annualized / price
            unknowns.append("forward_yield_is_annualized_from_latest_declared_not_company_guidance")

    return DividendSnapshot(
        ticker=ticker.upper(),
        events=tuple(declared),
        trailing_twelve_month=trailing,
        trailing_yield=trailing_yield,
        trailing_yield_label=DividendLabel.TRAILING,
        forward_yield=forward_yield,
        forward_yield_label=DividendLabel.FORECAST,
        annualized_from_latest_declared=annualized,
        price_for_yield=price,
        price_as_of=price_as_of,
        unknowns=tuple(unknowns),
        citations=tuple(citations[:20]),
    )


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
