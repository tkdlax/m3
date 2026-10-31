"""Deterministic + provider-neutral AI research adapters (no execution credentials)."""

from __future__ import annotations

from typing import Any

from dividend_agent.agents.interface import ResearchAgent
from dividend_agent.agents.schema import (
    ResearchBudget,
    StructuredResearchRequest,
    StructuredResearchResponse,
    validate_structured_response,
)
from dividend_agent.research.citations import Claim
from dividend_agent.research.memo import ResearchMemo, validate_memo_citations


class DeterministicResearchAgent(ResearchAgent):
    """Passes through deterministic memos; validates citation integrity."""

    @property
    def provider_name(self) -> str:
        return "deterministic"

    def enrich_memo(
        self,
        memo: ResearchMemo,
        *,
        source_excerpts: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> ResearchMemo:
        _ = _sanitize_excerpts(source_excerpts)
        problems = validate_memo_citations(memo)
        extras = dict(memo.extras)
        if problems:
            extras["citation_validation_problems"] = problems
        if extras == memo.extras:
            return memo
        return memo.model_copy(update={"extras": extras})

    def validate_claims(self, claims: list[Claim]) -> list[str]:
        problems: list[str] = []
        for claim in claims:
            if not claim.citations:
                problems.append("missing_citations")
            for cite in claim.citations:
                if cite.accession and not _looks_like_accession(cite.accession):
                    problems.append(f"malformed_accession:{cite.accession}")
        return problems

    def run_structured(
        self,
        request: StructuredResearchRequest,
    ) -> StructuredResearchResponse:
        problems = validate_memo_citations(request.memo)
        return StructuredResearchResponse(
            status="ok",
            provider=self.provider_name,
            claims=request.memo.claims,
            unknowns=request.memo.unknowns,
            validation_problems=tuple(problems),
        )


class LlmResearchAgentStub(ResearchAgent):
    """Provider-neutral LLM stub — schema + validation only; no network; no orders."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        budget: ResearchBudget | None = None,
    ) -> None:
        self._enabled = enabled
        self._budget = budget or ResearchBudget()

    @property
    def provider_name(self) -> str:
        return "llm_stub"

    def enrich_memo(
        self,
        memo: ResearchMemo,
        *,
        source_excerpts: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> ResearchMemo:
        request = StructuredResearchRequest(
            memo=memo,
            source_excerpts=tuple(_sanitize_excerpts(source_excerpts)),
            budget=self._budget,
        )
        resp = self.run_structured(request)
        extras = dict(memo.extras)
        extras["structured_research"] = resp.model_dump(mode="json")
        if not self._enabled:
            extras["llm_stub"] = "disabled"
            return memo.model_copy(update={"extras": extras, "agent_provider": self.provider_name})
        extras["llm_stub"] = "enabled_but_no_provider_configured"
        # Never invent claims without citations.
        return memo.model_copy(
            update={
                "extras": extras,
                "agent_provider": self.provider_name,
                "unknowns": tuple(
                    dict.fromkeys(
                        list(memo.unknowns) + list(resp.unknowns) + ["llm_provider_not_configured"]
                    )
                ),
            }
        )

    def validate_claims(self, claims: list[Claim]) -> list[str]:
        return DeterministicResearchAgent().validate_claims(claims)

    def run_structured(
        self,
        request: StructuredResearchRequest,
    ) -> StructuredResearchResponse:
        # Enforce budget fields exist (timeout/tokens) without calling a network provider.
        _ = request.budget.timeout_seconds
        _ = request.budget.max_tokens
        if not self._enabled:
            return StructuredResearchResponse(
                status="skipped",
                provider=self.provider_name,
                unknowns=("llm_stub_disabled",),
            )
        # Stub refuses to invent: return validation-only response.
        resp = StructuredResearchResponse(
            status="failed",
            provider=self.provider_name,
            unknowns=("llm_provider_not_configured", "no_invented_claims"),
            extras={
                "focus": list(request.focus),
                "excerpt_count": len(request.source_excerpts),
                "max_tokens": request.budget.max_tokens,
                "timeout_seconds": request.budget.timeout_seconds,
            },
        )
        problems = validate_structured_response(resp)
        if problems:
            return resp.model_copy(update={"validation_problems": tuple(problems)})
        return resp


def create_research_agent(
    *,
    provider: str = "deterministic",
    enabled: bool = False,
    budget: ResearchBudget | None = None,
) -> ResearchAgent:
    """Factory — never accepts brokerage credentials."""
    if provider in {"llm", "llm_stub"}:
        return LlmResearchAgentStub(enabled=enabled, budget=budget)
    return DeterministicResearchAgent()


def _sanitize_excerpts(excerpts: list[str] | None) -> list[str]:
    """Strip tool-invocation-looking lines; treat all content as untrusted text."""
    if not excerpts:
        return []
    cleaned: list[str] = []
    for raw in excerpts:
        text = raw.strip()
        lower = text.lower()
        if "submit_order" in lower or "place_order" in lower or "api_key" in lower:
            cleaned.append("[redacted untrusted tool-like content]")
            continue
        cleaned.append(text[:4000])
    return cleaned


def _looks_like_accession(value: str) -> bool:
    parts = value.split("-")
    return len(parts) == 3 and all(p.isdigit() for p in parts)
