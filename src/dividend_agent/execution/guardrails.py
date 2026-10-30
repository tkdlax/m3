"""Null execution guardrail checks — fail closed for auto-submit paths."""

from __future__ import annotations

from dividend_agent.config import AppConfig, AutoSubmitBlockedError


class NullGuardrailError(AutoSubmitBlockedError):
    """Raised when unresolved (null) §11/§21 guardrails would affect auto-submit."""


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
