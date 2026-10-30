"""Broker adapters (paper-only)."""

from dividend_agent.brokers.factory import create_broker_adapter
from dividend_agent.brokers.interface import (
    BrokerAdapter,
    PaperNotVerifiedError,
    PaperVerificationResult,
    require_verified_paper,
)

__all__ = [
    "BrokerAdapter",
    "PaperNotVerifiedError",
    "PaperVerificationResult",
    "create_broker_adapter",
    "require_verified_paper",
]
