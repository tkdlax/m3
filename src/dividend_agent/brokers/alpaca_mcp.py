"""Alpaca MCP adapter stub (M1). Paper-only; no live endpoint support."""

from __future__ import annotations

from dividend_agent.brokers.interface import BrokerAdapter, PaperVerificationResult
from dividend_agent.domain.models import PaperVerificationStatus


class AlpacaMcpAdapter(BrokerAdapter):
    """Placeholder until M1 wires official Alpaca MCP after capability discovery."""

    def verify_paper(self) -> PaperVerificationResult:
        return PaperVerificationResult(
            status=PaperVerificationStatus.UNVERIFIED,
            detail=(
                "Alpaca MCP adapter is a stub in M0. "
                "Positive paper verification is not available yet."
            ),
        )
