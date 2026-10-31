"""Filing narrative retrieval analysis — evidence required beyond a link."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dividend_agent.research.citations import Citation, Claim


@dataclass(frozen=True)
class NarrativeAnalysis:
    business: Claim | None
    risks: Claim | None
    debt: Claim | None
    cash_flow: Claim | None
    dividend_sustainability: Claim | None
    missing_sections: tuple[str, ...]
    source_accession: str | None
    has_substantive_evidence: bool


_SECTION_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "business": (
        re.compile(r"item\s*1[.\s].{0,40}business", re.I),
        re.compile(r"our\s+business", re.I),
    ),
    "risks": (
        re.compile(r"item\s*1a[.\s].{0,40}risk", re.I),
        re.compile(r"risk\s+factors", re.I),
    ),
    "debt": (
        re.compile(r"long[- ]term\s+debt", re.I),
        re.compile(r"indebtedness", re.I),
        re.compile(r"credit\s+facility", re.I),
    ),
    "cash_flow": (
        re.compile(r"cash\s+flows?\s+from\s+operating", re.I),
        re.compile(r"liquidity\s+and\s+capital\s+resources", re.I),
    ),
    "dividend_sustainability": (
        re.compile(r"dividends?\s+(per\s+share|declared|paid)", re.I),
        re.compile(r"dividend\s+policy", re.I),
    ),
}


def _excerpt_around(text: str, match: re.Match[str], *, radius: int = 180) -> str:
    start = max(0, match.start() - 40)
    end = min(len(text), match.end() + radius)
    return " ".join(text[start:end].split())


def analyze_filing_narrative(
    text: str,
    *,
    citation: Citation,
    min_chars: int = 400,
) -> NarrativeAnalysis:
    """Extract evidence snippets from filing text. Empty/short text ⇒ missing evidence."""
    cleaned = text or ""
    missing: list[str] = []
    claims: dict[str, Claim | None] = {
        "business": None,
        "risks": None,
        "debt": None,
        "cash_flow": None,
        "dividend_sustainability": None,
    }

    if len(cleaned.strip()) < min_chars:
        missing = list(claims.keys())
        return NarrativeAnalysis(
            business=None,
            risks=None,
            debt=None,
            cash_flow=None,
            dividend_sustainability=None,
            missing_sections=tuple(missing),
            source_accession=citation.accession,
            has_substantive_evidence=False,
        )

    for section, patterns in _SECTION_PATTERNS.items():
        found = None
        for pat in patterns:
            m = pat.search(cleaned)
            if m:
                found = m
                break
        if found is None:
            missing.append(section)
            continue
        excerpt = _excerpt_around(cleaned, found)
        cite = citation.model_copy(update={"excerpt": excerpt[:500], "fact_name": section})
        claims[section] = Claim(
            statement=f"{section} evidence from {citation.form or 'filing'}",
            citations=(cite,),
            uncertain=False,
        )

    substantive = (
        claims["business"] is not None
        and claims["risks"] is not None
        and len(cleaned.strip()) >= min_chars
    )
    return NarrativeAnalysis(
        business=claims["business"],
        risks=claims["risks"],
        debt=claims["debt"],
        cash_flow=claims["cash_flow"],
        dividend_sustainability=claims["dividend_sustainability"],
        missing_sections=tuple(missing),
        source_accession=citation.accession,
        has_substantive_evidence=substantive,
    )
