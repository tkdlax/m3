"""In-memory paper broker for tests (timeout / restart / idempotency)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from dividend_agent.brokers.interface import (
    M1_READ_CAPABILITIES,
    M4_SUBMIT_CAPABILITIES,
    BrokerAdapter,
    BrokerTransportError,
    PaperVerificationResult,
)
from dividend_agent.brokers.orders import PaperOrderRequest
from dividend_agent.brokers.types import (
    AccountSnapshot,
    ActivitySnapshot,
    OrderSnapshot,
    PositionSnapshot,
)
from dividend_agent.domain.models import PaperVerificationStatus


class FakePaperBroker(BrokerAdapter):
    """Deterministic paper broker with injectable timeout on submit."""

    def __init__(
        self,
        *,
        account_id: str = "paper-fake-001",
        fail_submit_times: int = 0,
        timeout_after_accept_once: bool = False,
        verified: bool = True,
    ) -> None:
        self.account_id = account_id
        self._fail_submit_times = fail_submit_times
        self._timeout_after_accept_once = timeout_after_accept_once
        self._submit_attempts = 0
        self._orders_by_client: dict[str, OrderSnapshot] = {}
        self._orders_by_broker: dict[str, OrderSnapshot] = {}
        self._verified = verified
        self.submit_count = 0

    def verify_paper(self) -> PaperVerificationResult:
        if not self._verified:
            return PaperVerificationResult(
                status=PaperVerificationStatus.FAILED,
                detail="fake broker configured as unverified",
            )
        return PaperVerificationResult(
            status=PaperVerificationStatus.VERIFIED_PAPER,
            account_id=self.account_id,
            detail="fake paper verified",
            capabilities=self.discover_capabilities(),
        )

    def discover_capabilities(self) -> tuple[str, ...]:
        return M1_READ_CAPABILITIES + M4_SUBMIT_CAPABILITIES

    def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(
            account_id=self.account_id,
            status="ACTIVE",
            cash=Decimal("100000"),
            buying_power=Decimal("100000"),
            equity=Decimal("100000"),
            portfolio_value=Decimal("100000"),
        )

    def list_positions(self) -> list[PositionSnapshot]:
        return []

    def list_orders(self, *, status: str = "all", limit: int = 100) -> list[OrderSnapshot]:
        return list(self._orders_by_broker.values())[:limit]

    def list_activities(
        self,
        *,
        activity_types: tuple[str, ...] | None = None,
        page_size: int = 100,
    ) -> list[ActivitySnapshot]:
        return []

    def submit_paper_order(self, request: PaperOrderRequest) -> OrderSnapshot:
        # Idempotent: same client_order_id returns existing order (no duplicate).
        existing = self._orders_by_client.get(request.client_order_id)
        if existing is not None:
            return existing

        self._submit_attempts += 1
        if self._submit_attempts <= self._fail_submit_times:
            raise BrokerTransportError("simulated network timeout on submit")

        self.submit_count += 1
        broker_id = f"brk-{uuid4().hex[:12]}"
        now = datetime.now(UTC)
        order = OrderSnapshot(
            broker_order_id=broker_id,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status="accepted",
            qty=request.qty,
            filled_qty=Decimal("0"),
            limit_price=request.limit_price,
            submitted_at=now,
            updated_at=now,
            raw={"client_order_id": request.client_order_id},
        )
        self._orders_by_client[request.client_order_id] = order
        self._orders_by_broker[broker_id] = order
        if self._timeout_after_accept_once:
            self._timeout_after_accept_once = False
            raise BrokerTransportError("simulated timeout after broker accepted")
        return order

    def get_order_by_client_id(self, client_order_id: str) -> OrderSnapshot | None:
        return self._orders_by_client.get(client_order_id)

    def cancel_paper_order(self, broker_order_id: str) -> OrderSnapshot:
        order = self._orders_by_broker[broker_order_id]
        canceled = order.model_copy(update={"status": "canceled"})
        self._orders_by_broker[broker_order_id] = canceled
        if order.client_order_id:
            self._orders_by_client[order.client_order_id] = canceled
        return canceled

    def fill(self, client_order_id: str, *, filled_qty: Decimal | None = None) -> OrderSnapshot:
        order = self._orders_by_client[client_order_id]
        qty = filled_qty if filled_qty is not None else order.qty
        filled = order.model_copy(
            update={
                "status": "filled",
                "filled_qty": qty,
                "filled_avg_price": order.limit_price,
            }
        )
        self._orders_by_client[client_order_id] = filled
        self._orders_by_broker[order.broker_order_id] = filled
        return filled
