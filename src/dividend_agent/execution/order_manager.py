"""Paper order lifecycle manager — idempotent submit, no live path (Milestone A)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

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
from dividend_agent.execution.authority import decision_client_order_id
from dividend_agent.execution.coverage import (
    broker_unencumbered_before_local,
    derive_coverage,
    underlying_from_intent,
)
from dividend_agent.execution.guardrails import (
    NullGuardrailError,
    assert_guardrails_resolved_for_auto_submit,
    numeric_limit_violations,
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
    """Single-worker paper execution with decision-stable client-order-id idempotency.

    Broker write credentials and submit calls stay on this gateway path. Callers
    must not bypass ``OrderManager`` to reach ``submit_paper_order``.
    """

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
        # Overlay for tests; pause is still re-read from live os.environ on submit.
        self._environ = environ if environ is not None else dict(os.environ)

    def client_order_id_for_intent(self, intent: TradeIntent) -> str:
        """Stable idempotency key from logical decision identity (restart-safe)."""
        return decision_client_order_id(intent)

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

        # Always prefer independently derived coverage; caller-supplied coverage
        # is only a fallback when the broker cannot be queried (tests).
        effective = self._resolve_coverage(intent, coverage, client_order_id=client_order_id)
        hard_reasons, soft_reasons = self._classify_blocks(
            intent,
            coverage=effective,
            exception_reasons=exception_reasons or [],
            client_order_id=client_order_id,
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
            self._repo.release_reservation(client_order_id, status="released")
            self._transition(
                client_order_id,
                OrderState.VALIDATED,
                OrderState.REJECTED,
                detail=";".join(hard_reasons),
            )
            return self._repo.get_order(client_order_id)  # type: ignore[return-value]

        if soft_reasons:
            # Soft path still reserves shares so concurrent intents cannot reuse them.
            if not self._try_reserve(intent, client_order_id):
                self._transition(
                    client_order_id,
                    OrderState.VALIDATED,
                    OrderState.REJECTED,
                    detail="reservation_conflict",
                )
                return self._repo.get_order(client_order_id)  # type: ignore[return-value]
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

        if not self._try_reserve(intent, client_order_id):
            self._transition(
                client_order_id,
                OrderState.VALIDATED,
                OrderState.REJECTED,
                detail="reservation_conflict",
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

        intent = TradeIntent.model_validate_json(row["intent_json"])
        hard, _soft = self._classify_blocks(
            intent,
            coverage=self._resolve_coverage(intent, None, client_order_id=client_order_id),
            exception_reasons=[],
            client_order_id=client_order_id,
        )
        if hard or row.get("hard_block"):
            self._repo.release_reservation(client_order_id, status="released")
            self._transition(
                client_order_id,
                OrderState(row["state"]),
                OrderState.REJECTED,
                detail=";".join(hard) if hard else "prior_hard_block",
            )
            raise OrderManagerError(f"revalidation hard blocks: {hard or ['prior_hard_block']}")

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
        # Before (re)submit: query broker by client order id. Lookup failure must
        # preserve uncertainty — never treat it as proof of absence.
        try:
            existing = self._broker.get_order_by_client_id(client_order_id)
        except Exception as exc:  # noqa: BLE001
            self._transition(
                client_order_id,
                OrderState.SUBMITTED,
                OrderState.RECONCILIATION_NEEDED,
                detail=f"lookup_failed:{exc}",
            )
            return self._repo.get_order(client_order_id)  # type: ignore[return-value]

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
            # Timeout / network: query again; empty confirmed lookup stays recon —
            # do NOT retry submit (duplicate risk under ambiguous accept).
            self._repo.append_event(
                client_order_id,
                "submit_timeout",
                from_state=OrderState.SUBMITTED.value,
                to_state=OrderState.RECONCILIATION_NEEDED.value,
                detail=str(exc),
            )
            try:
                recovered = self._broker.get_order_by_client_id(client_order_id)
            except Exception as lookup_exc:  # noqa: BLE001
                self._transition(
                    client_order_id,
                    OrderState.SUBMITTED,
                    OrderState.RECONCILIATION_NEEDED,
                    detail=f"timeout_lookup_failed:{lookup_exc}",
                )
                return self._repo.get_order(client_order_id)  # type: ignore[return-value]
            if recovered is not None:
                self._apply_broker_snapshot(client_order_id, recovered)
                return self._repo.get_order(client_order_id)  # type: ignore[return-value]
            self._transition(
                client_order_id,
                OrderState.SUBMITTED,
                OrderState.RECONCILIATION_NEEDED,
                detail="timeout_no_broker_order_yet",
            )
            return self._repo.get_order(client_order_id)  # type: ignore[return-value]

        self._apply_broker_snapshot(client_order_id, snapshot)
        return self._repo.get_order(client_order_id)  # type: ignore[return-value]

    def recover_after_restart(self, client_order_id: str) -> dict[str, Any]:
        """Resume ambiguous SUBMITTED / RECONCILIATION_NEEDED from broker truth."""
        row = self._repo.get_order(client_order_id)
        if row is None:
            raise OrderManagerError(f"unknown order {client_order_id}")
        verification = self._broker.verify_paper()
        require_verified_paper(verification)
        try:
            existing = self._broker.get_order_by_client_id(client_order_id)
        except Exception as exc:  # noqa: BLE001
            self._transition(
                client_order_id,
                OrderState(row["state"]),
                OrderState.RECONCILIATION_NEEDED,
                detail=f"recovery_lookup_failed:{exc}",
            )
            return self._repo.get_order(client_order_id)  # type: ignore[return-value]
        if existing is not None:
            self._apply_broker_snapshot(client_order_id, existing)
            return self._repo.get_order(client_order_id)  # type: ignore[return-value]
        # Confirmed absence — revalidate coverage before any resubmit.
        intent = TradeIntent.model_validate_json(row["intent_json"])
        hard, _ = self._classify_blocks(
            intent,
            coverage=self._resolve_coverage(intent, None, client_order_id=client_order_id),
            exception_reasons=[],
            client_order_id=client_order_id,
        )
        if hard:
            self._repo.release_reservation(client_order_id, status="released")
            self._transition(
                client_order_id,
                OrderState(row["state"]),
                OrderState.REJECTED,
                detail=";".join(hard),
            )
            raise OrderManagerError(f"recovery hard blocks: {hard}")
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
        intent = TradeIntent.model_validate_json(row["intent_json"])
        hard, _ = self._classify_blocks(
            intent,
            coverage=self._resolve_coverage(intent, None, client_order_id=client_order_id),
            exception_reasons=[],
            client_order_id=client_order_id,
        )
        if hard or row.get("hard_block"):
            self._repo.release_reservation(client_order_id, status="released")
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

    def _resolve_coverage(
        self,
        intent: TradeIntent,
        coverage: CoverageState | None,
        *,
        client_order_id: str | None,
    ) -> CoverageState | None:
        try:
            derived = derive_coverage(
                broker=self._broker,
                repo=self._repo,
                intent=intent,
                exclude_client_order_id=client_order_id,
            )
        except Exception:  # noqa: BLE001 — derivation failure is missing coverage
            derived = None
        if derived is not None:
            return derived
        return coverage

    def _try_reserve(self, intent: TradeIntent, client_order_id: str) -> bool:
        # Equity buys add shares — no share reservation / encumbrance.
        if (intent.extras or {}).get("intent_kind") == "stock_buy" or (
            intent.side.lower() in {"buy", "buy_to_cover"} and intent.strike is None
        ):
            return True
        underlying = underlying_from_intent(intent)
        if underlying is None:
            return False
        deliverable = intent.deliverable_shares or 100
        needed = Decimal(deliverable) * intent.quantity
        avail = broker_unencumbered_before_local(broker=self._broker, intent=intent)
        if avail is None:
            return False
        return self._repo.try_reserve_shares(
            reservation_id=str(uuid4()),
            client_order_id=client_order_id,
            account_id=intent.account_id,
            underlying=underlying,
            shares=str(needed),
            available_unencumbered=str(avail),
        )

    def refresh_from_broker(self, client_order_id: str) -> dict[str, Any]:
        """Sync local order state from broker truth (fills / partials)."""
        row = self._repo.get_order(client_order_id)
        if row is None:
            raise OrderManagerError(f"unknown order {client_order_id}")
        try:
            existing = self._broker.get_order_by_client_id(client_order_id)
        except Exception as exc:  # noqa: BLE001
            self._transition(
                client_order_id,
                OrderState(row["state"]),
                OrderState.RECONCILIATION_NEEDED,
                detail=f"refresh_lookup_failed:{exc}",
            )
            return self._repo.get_order(client_order_id)  # type: ignore[return-value]
        if existing is None:
            return row
        self._apply_broker_snapshot(client_order_id, existing)
        return self._repo.get_order(client_order_id)  # type: ignore[return-value]

    def _classify_blocks(
        self,
        intent: TradeIntent,
        *,
        coverage: CoverageState | None,
        exception_reasons: list[str],
        client_order_id: str | None = None,
    ) -> tuple[list[str], list[str]]:
        hard: list[str] = []
        soft: list[str] = list(exception_reasons)

        if intent.extras.get("live") or intent.extras.get("environment") == "live":
            hard.append("live_destination")

        is_stock_buy = (intent.extras or {}).get("intent_kind") == "stock_buy" or (
            intent.side.lower() in {"buy", "buy_to_cover"} and intent.strike is None
        )

        if coverage is None:
            # Stock buys may proceed with a synthetic empty coverage once underlying known.
            if is_stock_buy:
                underlying = underlying_from_intent(intent)
                if underlying is None:
                    hard.append("missing_underlying")
                    return hard, soft
                from dividend_agent.portfolio.lots import CoverageState as _CS

                coverage = _CS(symbol=underlying)
            else:
                hard.append("missing_coverage")
                return hard, soft

        expected_account: str | None = None
        try:
            account = self._broker.get_account()
            expected_account = account.account_id
            positions = self._broker.list_positions()
            for reason in numeric_limit_violations(
                config=self._config,
                intent=intent,
                account=account,
                positions=positions,
            ):
                key = reason.split(":", 1)[0]
                if key not in hard:
                    hard.append(key)
        except Exception as exc:  # noqa: BLE001
            hard.append(f"account_state_unavailable:{type(exc).__name__}")

        coverage_hard_keys = {
            "missing_wac",
            "missing_strike",
            "missing_underlying",
            "missing_deliverable_shares",
            "invalid_quantity",
            "account_mismatch",
            "underlying_mismatch",
            "unsupported_order_type",
            "unsupported_side_for_covered_call",
            "nonstandard_deliverable_unsupported",
            "insufficient_unencumbered_shares",
            "strike_below_wac",
            "missing_limit_price_for_notional_check",
        }
        for reason in validate_intent_coverage(
            intent, coverage, expected_account_id=expected_account
        ):
            key = reason.split(":", 1)[0]
            if key in HARD_BLOCK_REASONS or key in coverage_hard_keys:
                mapped = key
                if "strike_below_wac" in reason:
                    mapped = "strike_below_wac"
                elif "insufficient_unencumbered" in reason:
                    mapped = "insufficient_unencumbered_shares"
                if mapped not in hard:
                    hard.append(mapped)
            else:
                soft.append(reason)

        _ = client_order_id  # reserved for future reservation-aware classify
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
        if new_state in {
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
        }:
            self._repo.release_reservation(client_order_id, status="consumed")
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
