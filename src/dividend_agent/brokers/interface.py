"""Broker-agnostic adapter interface (spec §7.1). M0: types + fail-closed stubs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from dividend_agent.domain.models import PaperVerificationStatus


@dataclass(frozen=True)
class PaperVerificationResult:
    status: PaperVerificationStatus
    account_id: str | None = None
    detail: str = ""
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_verified_paper(self) -> bool:
        return self.status == PaperVerificationStatus.VERIFIED_PAPER


class BrokerAdapter(ABC):
    """Narrow paper-trading broker contract. Live routes are out of scope for v1."""

    @abstractmethod
    def verify_paper(self) -> PaperVerificationResult:
        """Positively attest paper environment or fail closed."""


class UnconfiguredBrokerAdapter(BrokerAdapter):
    """Default adapter before credentials/MCP wiring (M1). Always fails closed."""

    def verify_paper(self) -> PaperVerificationResult:
        return PaperVerificationResult(
            status=PaperVerificationStatus.UNVERIFIED,
            detail=(
                "No broker credentials or paper verification configured. "
                "M0 refuses to treat the account as verified paper."
            ),
        )


def require_verified_paper(result: PaperVerificationResult) -> None:
    """Raise if paper verification did not positively succeed."""
    if not result.is_verified_paper:
        raise PaperNotVerifiedError(result)


class PaperNotVerifiedError(RuntimeError):
    def __init__(self, result: PaperVerificationResult) -> None:
        self.result = result
        super().__init__(
            f"paper verification failed closed: status={result.status.value} "
            f"detail={result.detail!r}"
        )
