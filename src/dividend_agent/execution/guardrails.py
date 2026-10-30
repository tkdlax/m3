"""Null + numeric execution guardrail checks — fail closed for auto-submit."""

from __future__ import annotations

from dividend_agent.brokers.types import AccountSnapshot, PositionSnapshot
from dividend_agent.config import AppConfig, AutoSubmitBlockedError
from dividend_agent.domain.models import TradeIntent
from dividend_agent.strategy.allocation import evaluate_allocation_limits


class NullGuardrailError(AutoSubmitBlockedError):
    """Raised when unresolved (null) §11/§21 guardrails would affect auto-submit."""


class LimitExceededError(RuntimeError):
    """Raised when a resolved numeric §21 limit would be violated."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("numeric limit exceeded: " + "; ".join(reasons))


def null_execution_guardrails(config: AppConfig) -> list[str]:
    """Return unresolved execution guardrail field names."""
    return list(config.execution.unresolved_guardrails)


def assert_guardrails_resolved_for_auto_submit(config: AppConfig) -> None:
    """Fail closed if any auto-submit blocker remains.

    Research / paper_dry_run may proceed with null limits; only paths that would
    submit orders must call this (or ``AppConfig.assert_may_auto_submit``).
    """
    blockers = config.auto_submit_blockers()
    if blockers:
        raise NullGuardrailError(blockers)


def numeric_limit_violations(
    *,
    config: AppConfig,
    intent: TradeIntent,
    account: AccountSnapshot,
    positions: list[PositionSnapshot],
) -> list[str]:
    """Compare the intent to resolved numeric §21 limits (empty if all pass)."""
    return evaluate_allocation_limits(
        config=config,
        intent=intent,
        account=account,
        positions=positions,
    )


def assert_numeric_limits(
    *,
    config: AppConfig,
    intent: TradeIntent,
    account: AccountSnapshot,
    positions: list[PositionSnapshot],
) -> None:
    reasons = numeric_limit_violations(
        config=config, intent=intent, account=account, positions=positions
    )
    if reasons:
        raise LimitExceededError(reasons)
