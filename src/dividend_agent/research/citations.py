"""Citation and evidence types for research outputs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Citation(BaseModel):
    """Traceable source reference (spec §7.2 / §10)."""

    model_config = ConfigDict(frozen=True)

    source: str
    ticker: str | None = None
    cik: str | None = None
    accession: str | None = None
    form: str | None = None
    filing_date: date | None = None
    report_period: date | None = None
    url: str | None = None
    fact_name: str | None = None
    excerpt: str | None = None
    retrieved_at: datetime | None = None

    @field_validator("accession")
    @classmethod
    def _normalize_accession(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Keep dash form when provided; strip spaces.
        return value.strip()

    def display(self) -> str:
        parts = [self.source]
        if self.form:
            parts.append(self.form)
        if self.accession:
            parts.append(self.accession)
        if self.fact_name:
            parts.append(self.fact_name)
        if self.url:
            parts.append(self.url)
        return " | ".join(parts)


class Claim(BaseModel):
    """Typed research claim with required citations (LLM or deterministic)."""

    model_config = ConfigDict(frozen=True)

    statement: str
    citations: tuple[Citation, ...] = ()
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    uncertain: bool = False

    @field_validator("citations")
    @classmethod
    def _tuple_citations(cls, value: tuple[Citation, ...] | list[Citation]) -> tuple[Citation, ...]:
        return tuple(value)


class CriterionResult(BaseModel):
    """Pass/fail/review for a single qualification criterion."""

    model_config = ConfigDict(frozen=True)

    name: str
    status: str  # pass | fail | review | missing
    detail: str = ""
    evidence: tuple[Citation, ...] = ()
    extras: dict[str, Any] = Field(default_factory=dict)
