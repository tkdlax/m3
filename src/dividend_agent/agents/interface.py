"""Provider-neutral ResearchAgent interface (spec §7.3).

Implementations must not receive order-submission credentials or tools.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from dividend_agent.research.citations import Claim
from dividend_agent.research.memo import ResearchMemo


class ResearchAgent(ABC):
    """Read-only research enrichment. Never places orders."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    def enrich_memo(
        self,
        memo: ResearchMemo,
        *,
        source_excerpts: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> ResearchMemo:
        """Return an updated memo with typed claims/citations; may no-op."""

    @abstractmethod
    def validate_claims(self, claims: list[Claim]) -> list[str]:
        """Return validation problems (e.g. missing citations)."""
