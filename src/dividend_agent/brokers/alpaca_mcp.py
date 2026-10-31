"""Alpaca MCP adapter stub.

Preferred tool interface once official MCP capability names/schemas are
verified for unattended paper operation (spec §7.1). Trading REST remains in
:class:`AlpacaPaperAdapter`; market data REST is :class:`AlpacaMarketDataRestAdapter`
(ADR 0004). Any future MCP session for agents must stay **read-only** — never
expose submit/cancel tools.
"""

from __future__ import annotations

from dividend_agent.brokers.interface import (
    BrokerAdapter,
    PaperVerificationResult,
)
from dividend_agent.brokers.types import (
    AccountSnapshot,
    ActivitySnapshot,
    OrderSnapshot,
    PositionSnapshot,
)
from dividend_agent.domain.models import PaperVerificationStatus


class AlpacaMcpAdapter(BrokerAdapter):
    """Placeholder until MCP capability discovery is implemented."""

    def verify_paper(self) -> PaperVerificationResult:
        return PaperVerificationResult(
            status=PaperVerificationStatus.UNVERIFIED,
            detail=(
                "Alpaca MCP adapter is not wired in M1. Use AlpacaPaperAdapter "
                "(REST) via ALPACA_PAPER_* credentials, or wait for MCP discovery."
            ),
        )

    def discover_capabilities(self) -> tuple[str, ...]:
        return ()

    def get_account(self) -> AccountSnapshot:
        from dividend_agent.brokers.interface import PaperNotVerifiedError

        raise PaperNotVerifiedError(self.verify_paper())

    def list_positions(self) -> list[PositionSnapshot]:
        from dividend_agent.brokers.interface import PaperNotVerifiedError

        raise PaperNotVerifiedError(self.verify_paper())

    def list_orders(self, *, status: str = "all", limit: int = 100) -> list[OrderSnapshot]:
        from dividend_agent.brokers.interface import PaperNotVerifiedError

        raise PaperNotVerifiedError(self.verify_paper())

    def list_activities(
        self,
        *,
        activity_types: tuple[str, ...] | None = None,
        page_size: int = 100,
    ) -> list[ActivitySnapshot]:
        from dividend_agent.brokers.interface import PaperNotVerifiedError

        raise PaperNotVerifiedError(self.verify_paper())
