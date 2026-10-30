"""Broker paper verification fails closed without verified paper."""

from __future__ import annotations

import pytest

from dividend_agent.brokers.alpaca_mcp import AlpacaMcpAdapter
from dividend_agent.brokers.factory import create_broker_adapter
from dividend_agent.brokers.interface import (
    PaperNotVerifiedError,
    UnconfiguredBrokerAdapter,
    require_verified_paper,
)
from dividend_agent.domain.models import PaperVerificationStatus


@pytest.mark.parametrize("adapter_cls", [UnconfiguredBrokerAdapter, AlpacaMcpAdapter])
def test_stub_adapters_fail_closed(adapter_cls: type) -> None:
    result = adapter_cls().verify_paper()
    assert result.status != PaperVerificationStatus.VERIFIED_PAPER
    assert result.is_verified_paper is False
    with pytest.raises(PaperNotVerifiedError):
        require_verified_paper(result)


def test_factory_without_creds_is_unconfigured() -> None:
    adapter = create_broker_adapter(environ={})
    assert isinstance(adapter, UnconfiguredBrokerAdapter)
    with pytest.raises(PaperNotVerifiedError):
        require_verified_paper(adapter.verify_paper())
