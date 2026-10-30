"""Optional Alpaca paper SDK/REST fallback stub (M1). No live trading path."""

from __future__ import annotations

from dividend_agent.brokers.interface import BrokerAdapter, PaperVerificationResult
from dividend_agent.domain.models import PaperVerificationStatus


class AlpacaPaperAdapter(BrokerAdapter):
    """Placeholder REST/SDK adapter. Credentials are not consumed in M0."""

    def verify_paper(self) -> PaperVerificationResult:
        return PaperVerificationResult(
            status=PaperVerificationStatus.UNVERIFIED,
            detail=(
                "Alpaca paper SDK/REST adapter is a stub in M0. "
                "Refusing to claim paper verification without a real check."
            ),
        )
