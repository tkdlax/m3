"""Deterministic + optional LLM stub research agents (no execution credentials)."""

from __future__ import annotations

from typing import Any

from dividend_agent.agents.interface import ResearchAgent
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
        # Treat excerpts as untrusted; do not execute tool-like strings.
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


class LlmResearchAgentStub(ResearchAgent):
    """Optional LLM provider stub — disabled by default; no network, no orders."""

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled

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
        if not self._enabled:
            return memo
        # Stub: refuse to invent claims. Mark that LLM was skipped.
        extras = dict(memo.extras)
        extras["llm_stub"] = "enabled_but_no_provider_configured"
        _ = _sanitize_excerpts(source_excerpts)
        return memo.model_copy(update={"extras": extras, "agent_provider": self.provider_name})

    def validate_claims(self, claims: list[Claim]) -> list[str]:
        return DeterministicResearchAgent().validate_claims(claims)


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
    # e.g. 0000051462-24-000012
    parts = value.split("-")
    return len(parts) == 3 and all(p.isdigit() for p in parts)
