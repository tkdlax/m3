"""Dividend history normalization (event/period based — not XBRL-end-as-ex-date)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dividend_agent.research.citations import Citation
from dividend_agent.research.periods import PeriodKind, classify_period
from dividend_agent.research.sec_edgar import SecEdgarClient


class DividendLabel(StrEnum):
    DECLARED = "declared"
    FORECAST = "forecast"
    TRAILING = "trailing"
    SPECIAL = "special"
    UNKNOWN = "unknown"


class DividendEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: Decimal
    label: DividendLabel
    ex_date: date | None = None
    payment_date: date | None = None
    period_end: date | None = None  # XBRL period end — NOT an ex-date
    declaration_date: date | None = None
    frequency_hint: str | None = None  # quarterly | annual | special | unknown
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


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _infer_frequency(events: list[DividendEvent]) -> str:
    """Infer frequency from gaps between period ends (not payment/ex dates)."""
    ends = sorted({e.period_end for e in events if e.period_end})
    if len(ends) < 2:
        return "unknown"
    gaps = [(ends[i] - ends[i - 1]).days for i in range(1, len(ends))]
    avg = sum(gaps) / len(gaps)
    if 70 <= avg <= 110:
        return "quarterly"
    if 340 <= avg <= 390:
        return "annual"
    return "unknown"


def normalize_dividend_events(raw_events: list[DividendEvent]) -> list[DividendEvent]:
    """Deduplicate overlapping periods; flag specials; never set ex_date from XBRL end."""
    # Group by period_end + amount; keep latest filed citation
    by_key: dict[tuple[date | None, Decimal], DividendEvent] = {}
    for event in raw_events:
        # Explicit: period_end is not ex_date
        cleaned = event.model_copy(
            update={
                "ex_date": None if event.ex_date == event.period_end else event.ex_date,
                "payment_date": event.payment_date
                if event.payment_date != event.period_end
                else None,
            }
        )
        key = (cleaned.period_end, cleaned.amount)
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = cleaned
            continue
        prev_filed = prev.declaration_date or prev.period_end or date.min
        new_filed = cleaned.declaration_date or cleaned.period_end or date.min
        if new_filed >= prev_filed:
            by_key[key] = cleaned

    events = list(by_key.values())
    freq = _infer_frequency(events)
    median = None
    amounts = sorted(e.amount for e in events if e.label != DividendLabel.SPECIAL)
    if amounts:
        median = amounts[len(amounts) // 2]

    normalized: list[DividendEvent] = []
    for event in events:
        label = event.label
        frequency = freq
        if median is not None and event.amount >= median * Decimal("2.5"):
            label = DividendLabel.SPECIAL
            frequency = "special"
        elif freq == "quarterly":
            frequency = "quarterly"
        elif freq == "annual":
            frequency = "annual"
        normalized.append(event.model_copy(update={"label": label, "frequency_hint": frequency}))
    normalized.sort(key=lambda e: e.period_end or date.min, reverse=True)
    return normalized


def extract_dividends_from_companyfacts(
    companyfacts: dict[str, Any],
    *,
    ticker: str,
    cik: str,
    price: Decimal | None = None,
    price_as_of: date | None = None,
) -> DividendSnapshot:
    """Pull common stock cash dividends from us-gaap when present.

    Yields are only computed when price is supplied by an authorized market feed.
    XBRL period ends are **not** treated as payment or ex-dividend dates.
    """
    us_gaap = (companyfacts.get("facts") or {}).get("us-gaap") or {}
    tags = (
        "CommonStockDividendsPerShareDeclared",
        "PaymentsOfDividendsCommonStock",
    )
    raw_events: list[DividendEvent] = []
    citations: list[Citation] = []
    unknowns: list[str] = []

    for tag in tags:
        node = us_gaap.get(tag)
        if not isinstance(node, dict):
            continue
        units = node.get("units") or {}
        series = units.get("USD/shares") or units.get("USD") or []
        if not isinstance(series, list):
            continue
        for item in series:
            if not isinstance(item, dict) or item.get("val") is None:
                continue
            amount = Decimal(str(item["val"]))
            if tag == "PaymentsOfDividendsCommonStock" and "USD/shares" not in units:
                continue
            end = _parse_date(item.get("end"))
            start = _parse_date(item.get("start"))
            filed = _parse_date(item.get("filed"))
            accession = str(item["accn"]) if item.get("accn") else None
            form = str(item["form"]) if item.get("form") else None
            frame = str(item["frame"]) if item.get("frame") else None
            period = classify_period(end=end, start=start, frame=frame, form=form)
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
            raw_events.append(
                DividendEvent(
                    amount=amount,
                    label=DividendLabel.DECLARED,
                    ex_date=None,  # never invent from XBRL end
                    payment_date=None,  # period end ≠ payment date
                    period_end=end,
                    declaration_date=filed,
                    frequency_hint=(
                        "quarterly"
                        if period.kind == PeriodKind.QUARTERLY
                        else ("annual" if period.kind == PeriodKind.ANNUAL else "unknown")
                    ),
                    citation=citation,
                )
            )
            citations.append(citation)

    if not raw_events:
        unknowns.append("no_declared_dividend_facts_in_companyfacts")

    events = normalize_dividend_events(raw_events)
    unknowns.append("xbrl_period_end_is_not_ex_or_payment_date")

    # Trailing TTM: only sum distinct quarterly periods (not overlapping annual+quarter).
    quarterly = [
        e for e in events if e.label == DividendLabel.DECLARED and e.frequency_hint == "quarterly"
    ]
    specials = [e for e in events if e.label == DividendLabel.SPECIAL]
    if specials:
        unknowns.append(f"special_dividends_excluded_from_ttm={len(specials)}")

    trailing: Decimal | None = None
    annualized: Decimal | None = None
    if quarterly:
        # Deduped by period_end already; take up to 4 most recent quarters
        last_four = quarterly[:4]
        if len(last_four) == 4:
            trailing = sum((e.amount for e in last_four), Decimal("0"))
        else:
            unknowns.append("trailing_ttm_incomplete_fewer_than_4_distinct_quarters")
            trailing = sum((e.amount for e in last_four), Decimal("0"))
            unknowns.append("trailing_ttm_partial_sum_not_proven_four_quarters")
        annualized = quarterly[0].amount * Decimal("4")
    elif events:
        # Non-quarterly: do not pretend last-4 sum is TTM
        unknowns.append("non_quarterly_dividend_frequency_ttm_not_assumed")
        if events[0].frequency_hint == "annual":
            trailing = events[0].amount
            annualized = events[0].amount
        else:
            unknowns.append("cannot_annualize_unknown_dividend_frequency")

    trailing_yield = None
    forward_yield = None
    if price is None or price <= 0:
        unknowns.append("market_price_missing_yields_not_computed")
    else:
        if trailing is not None and "trailing_ttm_partial_sum" not in ",".join(unknowns):
            # Still expose partial with label uncertainty
            trailing_yield = trailing / price
        elif trailing is not None:
            trailing_yield = trailing / price
            unknowns.append("trailing_yield_based_on_incomplete_quarter_set")
        if annualized is not None:
            forward_yield = annualized / price
            unknowns.append("forward_yield_is_annualized_from_latest_declared_not_company_guidance")

    return DividendSnapshot(
        ticker=ticker.upper(),
        events=tuple(events),
        trailing_twelve_month=trailing,
        trailing_yield=trailing_yield,
        trailing_yield_label=DividendLabel.TRAILING,
        forward_yield=forward_yield,
        forward_yield_label=DividendLabel.FORECAST,
        annualized_from_latest_declared=annualized,
        price_for_yield=price,
        price_as_of=price_as_of,
        unknowns=tuple(dict.fromkeys(unknowns)),
        citations=tuple(citations[:20]),
    )
