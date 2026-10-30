"""Broker paper verification fails closed without verified paper."""

from __future__ import annotations

import pytest

from dividend_agent.brokers.alpaca_mcp import AlpacaMcpAdapter
from dividend_agent.brokers.alpaca_paper import AlpacaPaperAdapter
from dividend_agent.brokers.interface import (
    PaperNotVerifiedError,
    UnconfiguredBrokerAdapter,
    require_verified_paper,
)
from dividend_agent.domain.models import PaperVerificationStatus


@pytest.mark.parametrize(
    "adapter_cls",
    [UnconfiguredBrokerAdapter, AlpacaMcpAdapter, AlpacaPaperAdapter],
)
def test_m0_adapters_fail_closed(adapter_cls: type) -> None:
    result = adapter_cls().verify_paper()
    assert result.status != PaperVerificationStatus.VERIFIED_PAPER
    assert result.is_verified_paper is False
    with pytest.raises(PaperNotVerifiedError):
        require_verified_paper(result)
