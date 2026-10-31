"""Structured AI research request/response schema (provider-neutral)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dividend_agent.research.citations import Claim
from dividend_agent.research.memo import ResearchMemo


class ResearchBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    timeout_seconds: float = Field(default=60.0, gt=0)
    max_tokens: int = Field(default=4000, ge=1)
    max_excerpts: int = Field(default=20, ge=0)


class StructuredResearchRequest(BaseModel):
    """Input to a provider-neutral research adapter. Never includes order credentials."""

    model_config = ConfigDict(frozen=True)

    memo: ResearchMemo
    source_excerpts: tuple[str, ...] = ()
    budget: ResearchBudget = Field(default_factory=ResearchBudget)
    focus: tuple[str, ...] = (
        "business",
        "risks",
        "debt",
        "cash_flow",
        "dividend_sustainability",
    )

    @field_validator("source_excerpts")
    @classmethod
    def _no_secrets_in_excerpts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        banned = ("api_key", "alpaca_paper", "submit_order", "secret")
        cleaned: list[str] = []
        for raw in value:
            lower = raw.lower()
            if any(b in lower for b in banned):
                cleaned.append("[redacted]")
            else:
                cleaned.append(raw[:8000])
        return tuple(cleaned)


class StructuredResearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "skipped", "failed"]
    provider: str
    claims: tuple[Claim, ...] = ()
    unknowns: tuple[str, ...] = ()
    validation_problems: tuple[str, ...] = ()
    extras: dict[str, Any] = Field(default_factory=dict)


def validate_structured_response(resp: StructuredResearchResponse) -> list[str]:
    problems: list[str] = list(resp.validation_problems)
    for claim in resp.claims:
        if not claim.citations:
            problems.append(f"claim_missing_citations:{claim.statement[:80]}")
    return problems
