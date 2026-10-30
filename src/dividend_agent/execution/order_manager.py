"""Paper order lifecycle manager — idempotent submit, no live path (M4)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from dividend_agent.brokers.interface import (
    BrokerAdapter,
    BrokerTransportError,
    PaperNotVerifiedError,
    require_verified_paper,
)
from dividend_agent.brokers.orders import PaperOrderRequest
from dividend_agent.config import AppConfig
from dividend_agent.domain.models import OperatingMode, TradeIntent
from dividend_agent.execution.approval_queue import ApprovalError, ApprovalQueue
from dividend_agent.execution.guardrails import (
    NullGuardrailError,
    assert_guardrails_resolved_for_auto_submit,
)
from dividend_agent.execution.kill_switch import (
    TradingPausedError,
    assert_trading_not_paused,
)
from dividend_agent.execution.states import HARD_BLOCK_REASONS, OrderState, can_transition
from dividend_agent.execution.validator import validate_intent_coverage
from dividend_agent.portfolio.lots import CoverageState
from dividend_agent.storage.execution_repository import ExecutionRepository


class OrderManagerError(RuntimeError):
    """Order lifecycle failure."""


class OrderManager:
    """Single-worker paper execution with client-order-id idempotency."""

    def __init__(
        self,
        *,
        config: AppConfig,
        broker: BrokerAdapter,
        repo: ExecutionRepository,
        approvals: ApprovalQueue | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._config = config
        self._broker = broker
        self._repo = repo
        self._approvals = approvals or ApprovalQueue(repo)
        self._environ = environ if environ is not None else dict(os.environ)

    def client_order_id_for_intent(self, intent: TradeIntent) -> str:
        """Stable idempotency key derived from intent id (restart-safe)."""
        return f"da-{intent.intent_id.hex}"

    def process_intent(
        self,
        intent: TradeIntent,
        *,
        coverage: CoverageState | None = None,
        exception_reasons: list[str] | None = None,
        force_dry_run: bool = False,
    ) -> dict[str, Any]:
        """Validate and either dry-run, queue approval, or submit paper order."""
        client_order_id = self.client_order_id_for_intent(intent)
        existing = self._repo.get_order(client_order_id)
        if existing and existing["state"] in {
            OrderState.SUBMITTED.value,
            OrderState.ACKNOWLEDGED.value,
            OrderState.FILLED.value,
            OrderState.PARTIALLY_FILLED.value,
        }:
            return existing

        hard_reasons, soft_reasons = self._classify_blocks(
            intent, coverage=coverage, exception_reasons=exception_reasons or []
        )
        now = _now()
        row = {
            "client_order_id": client_order_id,
            "intent_id": str(intent.intent_id),
            "account_id": intent.account_id,
            "symbol": intent.symbol,
            "side": intent.side,
            "qty": str(intent.quantity),
            "order_type": intent.order_type,
            "limit_price": str(intent.limit_price) if intent.limit_price is not None else None,
            "state": OrderState.PROPOSED.value,
            "broker_order_id": None,
            "broker_status": None,
            "filled_qty": None,
            "requires_approval": 1 if soft_reasons else 0,
            "hard_block": 1 if hard_reasons else 0,
            "rejection_reasons_json": json.dumps(hard_reasons + soft_reasons),
            "intent_json": intent.model_dump_json(),
            "created_at": now,
            "updated_at": now,
        }
        self._repo.upsert_local_order(row)
        self._transition(client_order_id, OrderState.PROPOSED, OrderState.VALIDATED)

        if hard_reasons:
            self._transition(
                client_order_id,
                OrderState.VALIDATED,
                OrderState.REJECTED,
                detail=";".join(hard_reasons),
            )
            return self._repo.get_order(client_order_id)  # type: ignore[return-value]

        if soft_reasons:
            approval_id = self._approvals.enqueue(
                client_order_id=client_order_id,
                approval_type="exception",
                reason=";".join(soft_reasons),
                snapshot={
                    "intent_id": str(intent.intent_id),
                    "soft_reasons": soft_reasons,
                    "hard_block_reasons": [],
                },
            )
            self._transition(
                client_order_id,
                OrderState.VALIDATED,
                OrderState.AWAITING_APPROVAL,
                detail=f"approval_id={approval_id}",
            )
            return self._repo.get_order(client_order_id)  # type: ignore[return-value]

        self._transition(client_order_id, OrderState.VALIDATED, OrderState.READY)

        if force_dry_run or self._config.mode != OperatingMode.PAPER_AUTO:
            self._repo.append_event(
                client_order_id,
                "dry_run_hold",
                from_state=OrderState.READY.value,
                to_state=OrderState.READY.value,
                detail=f"mode={self._config.mode.value}; order not submitted",
            )
            return self._repo.get_order(client_order_id)  # type: ignore[return-value]

        return self.submit_ready_order(client_order_id)

    def submit_ready_order(self, client_order_id: str) -> dict[str, Any]:
        """Submit a READY (or approved) order to paper broker with idempotency."""
        row = self._repo.get_order(client_order_id)
        if row is None:
            raise OrderManagerError(f"unknown order {client_order_id}")

        try:
            assert_trading_not_paused(environ=self._environ)
        except TradingPausedError as exc:
            self._transition(
                client_order_id,
                OrderState(row["state"]),
                OrderState.ERROR,
                detail=str(exc),
            )
            raise

        try:
            assert_guardrails_resolved_for_auto_submit(self._config)
        except NullGuardrailError as exc:
            self._transition(
                client_order_id,
                OrderState(row["state"]),
                OrderState.ERROR,
                detail=str(exc),
            )
            raise

        if self._config.mode != OperatingMode.PAPER_AUTO:
            raise OrderManagerError(
                f"submit requires paper_auto mode (current={self._config.mode.value})"
            )
        if not self._config.execution.auto_paper_orders_enabled:
            raise OrderManagerError("execution.auto_paper_orders_enabled is false")

        verification = self._broker.verify_paper()
        try:
            require_verified_paper(verification)
        except PaperNotVerifiedError:
            self._transition(
                client_order_id,
                OrderState(row["state"]),
                OrderState.ERROR,
                detail="paper_not_verified",
            )
            raise

        # Revalidate hard gates from stored intent before submit
        intent = TradeIntent.model_validate_json(row["intent_json"])
        hard, _soft = self._classify_blocks(intent, coverage=None, exception_reasons=[])
        # Coverage cannot be re-derived without lots — rely on prior hard_block flag
        if row.get("hard_block"):
            raise OrderManagerError("hard-blocked order cannot be submitted")
        if hard:
            self._transition(
                client_order_id,
                OrderState(row["state"]),
                OrderState.REJECTED,
                detail=";".join(hard),
            )
            raise OrderManagerError(f"revalidation hard blocks: {hard}")

        state = OrderState(row["state"])
        if state == OrderState.AWAITING_APPROVAL:
            raise OrderManagerError("order still awaiting approval")
        if state not in {
            OrderState.READY,
            OrderState.RECONCILIATION_NEEDED,
            OrderState.ERROR,
            OrderState.SUBMITTED,
        }:
            raise OrderManagerError(f"cannot submit from state {state.value}")

        if state == OrderState.READY:
            self._transition(client_order_id, OrderState.READY, OrderState.SUBMITTED)

        return self._submit_with_idempotency(client_order_id, intent)

    def _submit_with_idempotency(self, client_order_id: str, intent: TradeIntent) -> dict[str, Any]:
        # Before (re)submit: query broker by client order id
        existing = None
        try:
            existing = self._broker.get_order_by_client_id(client_order_id)
        except Exception as exc:  # noqa: BLE001 — treat lookup failure as recon needed
            self._transition(
                client_order_id,
                OrderState.SUBMITTED,
                OrderState.RECONCILIATION_NEEDED,
                detail=f"lookup_failed:{exc}",
            )

        if existing is not None:
            self._apply_broker_snapshot(client_order_id, existing)
            return self._repo.get_order(client_order_id)  # type: ignore[return-value]

        request = PaperOrderRequest(
            client_order_id=client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.quantity,
            order_type=intent.order_type,
            limit_price=intent.limit_price,
        )
        try:
            snapshot = self._broker.submit_paper_order(request)
        except BrokerTransportError as exc:
            # Timeout / network: query again before any retry
            self._repo.append_event(
                client_order_id,
                "submit_timeout",
                from_state=OrderState.SUBMITTED.value,
                to_state=OrderState.RECONCILIATION_NEEDED.value,
                detail=str(exc),
            )
            recovered = self._broker.get_order_by_client_id(client_order_id)
            if recovered is not None:
                self._apply_broker_snapshot(client_order_id, recovered)
                return self._repo.get_order(client_order_id)  # type: ignore[return-value]
            self._transition(
                client_order_id,
                OrderState.SUBMITTED,
                OrderState.RECONCILIATION_NEEDED,
                detail="timeout_no_broker_order_yet",
            )
            # Safe single retry after empty lookup
            snapshot = self._broker.submit_paper_order(request)

        self._apply_broker_snapshot(client_order_id, snapshot)
        return self._repo.get_order(client_order_id)  # type: ignore[return-value]

    def recover_after_restart(self, client_order_id: str) -> dict[str, Any]:
        """Resume ambiguous SUBMITTED / RECONCILIATION_NEEDED from broker truth."""
        row = self._repo.get_order(client_order_id)
        if row is None:
            raise OrderManagerError(f"unknown order {client_order_id}")
        verification = self._broker.verify_paper()
        require_verified_paper(verification)
        existing = self._broker.get_order_by_client_id(client_order_id)
        if existing is not None:
            self._apply_broker_snapshot(client_order_id, existing)
            return self._repo.get_order(client_order_id)  # type: ignore[return-value]
        # No broker order — safe to (re)submit once if still paper_auto
        TradeIntent.model_validate_json(row["intent_json"])  # ensure payload still valid
        self._transition(
            client_order_id,
            OrderState(row["state"]),
            OrderState.READY,
            detail="restart_recovery_no_broker_order",
        )
        return self.submit_ready_order(client_order_id)

    def apply_approval_and_submit(
        self, approval_id: str, *, decided_by: str = "operator"
    ) -> dict[str, Any]:
        approved = self._approvals.approve(approval_id, decided_by=decided_by)
        client_order_id = approved["client_order_id"]
        row = self._repo.get_order(client_order_id)
        if row is None:
            raise OrderManagerError("order missing for approval")
        if row.get("hard_block"):
            raise ApprovalError("hard-blocked order cannot proceed after approval")
        # Revalidate before moving to ready/submit
        intent = TradeIntent.model_validate_json(row["intent_json"])
        hard, _ = self._classify_blocks(intent, coverage=None, exception_reasons=[])
        if hard or row.get("hard_block"):
            self._transition(
                client_order_id,
                OrderState(row["state"]),
                OrderState.REJECTED,
                detail="hard_block_on_revalidation",
            )
            raise OrderManagerError("hard blocks after approval revalidation")
        self._transition(
            client_order_id,
            OrderState.AWAITING_APPROVAL,
            OrderState.READY,
            detail=f"approved:{approval_id}",
        )
        if self._config.mode == OperatingMode.PAPER_AUTO:
            return self.submit_ready_order(client_order_id)
        return self._repo.get_order(client_order_id)  # type: ignore[return-value]

    def _classify_blocks(
        self,
        intent: TradeIntent,
        *,
        coverage: CoverageState | None,
        exception_reasons: list[str],
    ) -> tuple[list[str], list[str]]:
        hard: list[str] = []
        soft: list[str] = list(exception_reasons)
        if coverage is not None:
            for reason in validate_intent_coverage(intent, coverage):
                key = reason.split(":", 1)[0]
                if key in HARD_BLOCK_REASONS or reason.startswith("strike_below_wac"):
                    hard.append(
                        "strike_below_wac"
                        if "strike_below_wac" in reason
                        else (
                            "insufficient_unencumbered_shares"
                            if "insufficient_unencumbered" in reason
                            else reason
                        )
                    )
                else:
                    soft.append(reason)
        # Live trading markers in extras
        if intent.extras.get("live") or intent.extras.get("environment") == "live":
            hard.append("live_destination")
        return hard, soft

    def _apply_broker_snapshot(self, client_order_id: str, snapshot: Any) -> None:
        row = self._repo.get_order(client_order_id)
        assert row is not None
        status = str(snapshot.status).lower()
        if status in {"new", "accepted", "pending_new"}:
            new_state = OrderState.ACKNOWLEDGED
        elif status == "partially_filled":
            new_state = OrderState.PARTIALLY_FILLED
        elif status == "filled":
            new_state = OrderState.FILLED
        elif status in {"canceled", "cancelled"}:
            new_state = OrderState.CANCELED
        elif status == "rejected":
            new_state = OrderState.REJECTED
        elif status == "expired":
            new_state = OrderState.EXPIRED
        else:
            new_state = OrderState.ACKNOWLEDGED
        current = OrderState(row["state"])
        # Force-progress from ambiguous submit/recon states using broker truth.
        force = current in {
            OrderState.SUBMITTED,
            OrderState.RECONCILIATION_NEEDED,
            OrderState.READY,
        }
        if current != new_state and (force or can_transition(current, new_state)):
            self._repo.append_event(
                client_order_id,
                "broker_sync",
                from_state=current.value,
                to_state=new_state.value,
                detail=status,
            )
        filled = snapshot.filled_qty
        fresh = self._repo.get_order(client_order_id)
        assert fresh is not None
        self._repo.upsert_local_order(
            {
                **fresh,
                "broker_order_id": snapshot.broker_order_id,
                "broker_status": snapshot.status,
                "filled_qty": str(filled) if filled is not None else fresh.get("filled_qty"),
                "state": new_state.value,
                "updated_at": _now(),
            }
        )

    def _transition(
        self,
        client_order_id: str,
        from_state: OrderState,
        to_state: OrderState,
        *,
        detail: str = "",
    ) -> None:
        row = self._repo.get_order(client_order_id)
        if row is None:
            raise OrderManagerError(f"unknown order {client_order_id}")
        current = OrderState(row["state"])
        if current == to_state:
            return
        if current != from_state and not can_transition(current, to_state):
            raise OrderManagerError(
                f"illegal transition {current.value} -> {to_state.value} "
                f"(expected from {from_state.value})"
            )
        if not can_transition(current, to_state):
            raise OrderManagerError(f"illegal transition {current.value} -> {to_state.value}")
        self._repo.upsert_local_order({**row, "state": to_state.value, "updated_at": _now()})
        self._repo.append_event(
            client_order_id,
            "state_transition",
            from_state=current.value,
            to_state=to_state.value,
            detail=detail,
        )


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def make_auto_config(base: AppConfig) -> AppConfig:
    """Test helper: paper_auto with example liquidity/limits filled (not for prod defaults)."""
    return base.model_copy(
        update={
            "mode": OperatingMode.PAPER_AUTO,
            "execution": base.execution.model_copy(
                update={
                    "auto_paper_orders_enabled": True,
                    "max_position_pct": 0.1,
                    "max_portfolio_order_notional_usd": 5000.0,
                    "min_cash_reserve_usd": 1000.0,
                    "min_option_bid_usd_per_share": 0.05,
                    "max_option_bid_ask_spread_pct": 0.5,
                    "min_option_open_interest": 10,
                    "max_new_stock_order_notional_usd": 2500.0,
                }
            ),
        }
    )
