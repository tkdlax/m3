"""Issuer research memo structure (spec §10)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from dividend_agent.research.citations import Citation, Claim, CriterionResult
from dividend_agent.research.company_facts import CompanyFactsSnapshot
from dividend_agent.research.dividends import DividendSnapshot
from dividend_agent.research.screener import MarketSnapshotInput, ScreenResult


class IssuerIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    cik: str
    entity_name: str | None = None
    sector: str | None = None
    business_summary: str | None = None
    listing: str | None = None
    optionable: bool | None = None


class ResearchMemo(BaseModel):
    """Sourced issuer memo — unknowns explicit; no fabricated data."""

    model_config = ConfigDict(frozen=True)

    memo_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    identity: IssuerIdentity
    price: Decimal | None = None
    price_as_of: date | None = None
    week52_high: Decimal | None = None
    week52_low: Decimal | None = None
    week52_position: Decimal | None = None
    dividends: DividendSnapshot | None = None
    financials: CompanyFactsSnapshot | None = None
    filings: tuple[Citation, ...] = ()
    risks: tuple[Claim, ...] = ()
    thesis: str = ""
    claims: tuple[Claim, ...] = ()
    criteria: tuple[CriterionResult, ...] = ()
    screen: ScreenResult | None = None
    unknowns: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()
    decision: Literal["accept", "reject", "review"] = "review"
    decision_reasons: tuple[str, ...] = ()
    data_freshness_days: int | None = None
    agent_provider: str = "deterministic"
    extras: dict[str, Any] = Field(default_factory=dict)


def build_deterministic_memo(
    *,
    identity: IssuerIdentity,
    filings: list[Citation],
    financials: CompanyFactsSnapshot,
    dividends: DividendSnapshot,
    market: MarketSnapshotInput,
    screen: ScreenResult,
    max_age_days: int,
) -> ResearchMemo:
    unknowns: list[str] = list(dividends.unknowns) + list(financials.missing)
    citations: list[Citation] = list(filings) + list(dividends.citations)
    for fact in financials.facts.values():
        if fact.citation:
            citations.append(fact.citation)

    week52_position = None
    if (
        market.price is not None
        and market.week52_high is not None
        and market.week52_low is not None
        and market.week52_high > market.week52_low
    ):
        week52_position = (market.price - market.week52_low) / (
            market.week52_high - market.week52_low
        )

    # Freshness: age of newest filing citation
    freshness = None
    filing_dates = [c.filing_date for c in filings if c.filing_date]
    if filing_dates:
        newest = max(filing_dates)
        freshness = (date.today() - newest).days
        if freshness > max_age_days:
            unknowns.append(f"filings_stale_age_days={freshness}")

    has_10k = any(c.form == "10-K" for c in filings)
    has_10q = any(c.form == "10-Q" for c in filings)
    if not has_10k:
        unknowns.append("missing_10k_in_recent_filings")
    if not has_10q:
        unknowns.append("missing_10q_in_recent_filings")

    claims: list[Claim] = []
    if financials.facts.get("revenue"):
        fact = financials.facts["revenue"]
        claims.append(
            Claim(
                statement=f"Latest tagged revenue fact {fact.name}={fact.value} {fact.unit}",
                citations=(fact.citation,) if fact.citation else (),
            )
        )
    if dividends.trailing_twelve_month is not None:
        claims.append(
            Claim(
                statement=(
                    f"Trailing declared dividend sum (up to 4 events)="
                    f"{dividends.trailing_twelve_month} "
                    f"(label={dividends.trailing_yield_label.value})"
                ),
                citations=dividends.citations[:3],
            )
        )

    thesis_parts = [
        f"Deterministic research memo for {identity.ticker} (CIK {identity.cik}).",
        "No LLM bankruptcy-safety assertion is made.",
    ]
    if screen.accepted:
        thesis_parts.append("Screen gates currently accept subject to operator review.")
    elif screen.review_flags:
        thesis_parts.append("Screen flagged for review: " + ", ".join(screen.review_flags))
    else:
        thesis_parts.append("Screen rejected: " + (", ".join(screen.reasons) or "see criteria"))

    decision: Literal["accept", "reject", "review"]
    decision_reasons: tuple[str, ...]
    if screen.accepted and not unknowns:
        decision = "accept"
        decision_reasons = ("screen_accepted",)
    elif any(c.status == "fail" for c in screen.criteria):
        decision = "reject"
        decision_reasons = tuple(screen.reasons) or ("screen_failed",)
    else:
        decision = "review"
        decision_reasons = (
            tuple(screen.review_flags) or tuple(unknowns) or ("insufficient_or_ambiguous_data",)
        )

    return ResearchMemo(
        identity=identity,
        price=market.price,
        price_as_of=market.price_as_of,
        week52_high=market.week52_high,
        week52_low=market.week52_low,
        week52_position=week52_position,
        dividends=dividends,
        financials=financials,
        filings=tuple(filings),
        thesis=" ".join(thesis_parts),
        claims=tuple(claims),
        criteria=screen.criteria,
        screen=screen,
        unknowns=tuple(dict.fromkeys(unknowns)),
        citations=tuple(_dedupe_citations(citations)),
        decision=decision,
        decision_reasons=decision_reasons,
        data_freshness_days=freshness,
        agent_provider="deterministic",
    )


def _dedupe_citations(items: list[Citation]) -> list[Citation]:
    seen: set[str] = set()
    out: list[Citation] = []
    for c in items:
        key = c.display()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def validate_memo_citations(memo: ResearchMemo) -> list[str]:
    """Return problems: claims without citations, or citations missing accession/url when SEC."""
    problems: list[str] = []
    for claim in memo.claims:
        if not claim.citations:
            problems.append(f"claim_missing_citations:{claim.statement[:80]}")
    for cite in memo.citations:
        if cite.source.startswith("sec") and not cite.accession and not cite.url:
            problems.append(f"sec_citation_incomplete:{cite.display()}")
    return problems
