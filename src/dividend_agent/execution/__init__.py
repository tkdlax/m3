"""Execution: guardrails, reconciliation, approvals, paper order manager."""

from dividend_agent.execution.approval_queue import ApprovalError, ApprovalQueue
from dividend_agent.execution.guardrails import (
    NullGuardrailError,
    assert_guardrails_resolved_for_auto_submit,
    null_execution_guardrails,
)
from dividend_agent.execution.kill_switch import TradingPausedError, is_trading_paused
from dividend_agent.execution.order_manager import OrderManager, OrderManagerError
from dividend_agent.execution.reconciliation import Reconciler, ReconcileResult
from dividend_agent.execution.states import HARD_BLOCK_REASONS, OrderState

__all__ = [
    "ApprovalError",
    "ApprovalQueue",
    "HARD_BLOCK_REASONS",
    "NullGuardrailError",
    "OrderManager",
    "OrderManagerError",
    "OrderState",
    "ReconcileResult",
    "Reconciler",
    "TradingPausedError",
    "assert_guardrails_resolved_for_auto_submit",
    "is_trading_paused",
    "null_execution_guardrails",
]
