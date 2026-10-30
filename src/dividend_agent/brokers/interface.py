"""Broker-agnostic adapter interface (spec §7.1). Paper-only; no live routes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from dividend_agent.brokers.types import (
    AccountSnapshot,
    ActivitySnapshot,
    OrderSnapshot,
    PositionSnapshot,
)
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


# Capabilities expected for M1 read path; discovery reports which are present.
M1_READ_CAPABILITIES: tuple[str, ...] = (
    "verify_paper",
    "get_account",
    "list_positions",
    "list_orders",
    "list_activities",
)


class BrokerAdapter(ABC):
    """Paper-trading broker contract. Order submission is out of scope for M1."""

    @abstractmethod
    def verify_paper(self) -> PaperVerificationResult:
        """Positively attest paper environment or fail closed."""

    @abstractmethod
    def discover_capabilities(self) -> tuple[str, ...]:
        """Return capability names available on this adapter instance."""

    @abstractmethod
    def get_account(self) -> AccountSnapshot:
        """Fetch account balances and status. Requires verified paper session."""

    @abstractmethod
    def list_positions(self) -> list[PositionSnapshot]:
        """Current stock/option positions."""

    @abstractmethod
    def list_orders(self, *, status: str = "all", limit: int = 100) -> list[OrderSnapshot]:
        """Open and/or historical orders (read-only)."""

    @abstractmethod
    def list_activities(
        self,
        *,
        activity_types: tuple[str, ...] | None = None,
        page_size: int = 100,
    ) -> list[ActivitySnapshot]:
        """Account activities (fills, dividends, assignments, etc.)."""


class UnconfiguredBrokerAdapter(BrokerAdapter):
    """Used when credentials are missing. Always fails closed."""

    def verify_paper(self) -> PaperVerificationResult:
        return PaperVerificationResult(
            status=PaperVerificationStatus.UNVERIFIED,
            detail=(
                "No broker credentials configured. Set ALPACA_PAPER_API_KEY and "
                "ALPACA_PAPER_API_SECRET (paper only). Refusing to treat the account "
                "as verified paper."
            ),
        )

    def discover_capabilities(self) -> tuple[str, ...]:
        return ()

    def get_account(self) -> AccountSnapshot:
        raise PaperNotVerifiedError(self.verify_paper())

    def list_positions(self) -> list[PositionSnapshot]:
        raise PaperNotVerifiedError(self.verify_paper())

    def list_orders(self, *, status: str = "all", limit: int = 100) -> list[OrderSnapshot]:
        raise PaperNotVerifiedError(self.verify_paper())

    def list_activities(
        self,
        *,
        activity_types: tuple[str, ...] | None = None,
        page_size: int = 100,
    ) -> list[ActivitySnapshot]:
        raise PaperNotVerifiedError(self.verify_paper())


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


class BrokerConfigError(RuntimeError):
    """Invalid broker configuration (e.g. live URL presented)."""


class BrokerTransportError(RuntimeError):
    """HTTP / transport failure talking to the broker."""
