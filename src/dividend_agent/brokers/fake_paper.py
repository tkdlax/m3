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
        cash: Decimal = Decimal("100000"),
        equity: Decimal = Decimal("100000"),
        positions: list[PositionSnapshot] | None = None,
        options_approved_level: int | None = 3,
        fail_lookup: bool = False,
    ) -> None:
        self.account_id = account_id
        self._fail_submit_times = fail_submit_times
        self._timeout_after_accept_once = timeout_after_accept_once
        self._submit_attempts = 0
        self._orders_by_client: dict[str, OrderSnapshot] = {}
        self._orders_by_broker: dict[str, OrderSnapshot] = {}
        self._verified = verified
        self.submit_count = 0
        self._cash = cash
        self._equity = equity
        self._positions = list(positions or [])
        self._options_approved_level = options_approved_level
        self._fail_lookup = fail_lookup

    def set_positions(self, positions: list[PositionSnapshot]) -> None:
        self._positions = list(positions)

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
            cash=self._cash,
            buying_power=self._cash,
            equity=self._equity,
            portfolio_value=self._equity,
            options_approved_level=self._options_approved_level,
        )

    def list_positions(self) -> list[PositionSnapshot]:
        return list(self._positions)

    def list_orders(self, *, status: str = "all", limit: int = 100) -> list[OrderSnapshot]:
        orders = list(self._orders_by_broker.values())
        if status == "open":
            open_statuses = {"new", "accepted", "pending_new", "partially_filled", "held", "open"}
            orders = [o for o in orders if (o.status or "").lower() in open_statuses]
        return orders[:limit]

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
        if self._fail_lookup:
            raise BrokerTransportError("simulated lookup failure")
        return self._orders_by_client.get(client_order_id)

    def cancel_paper_order(self, broker_order_id: str) -> OrderSnapshot:
        order = self._orders_by_broker[broker_order_id]
        canceled = order.model_copy(update={"status": "canceled"})
        self._orders_by_broker[broker_order_id] = canceled
        if order.client_order_id:
            self._orders_by_client[order.client_order_id] = canceled
        return canceled

    def fill(
        self,
        client_order_id: str,
        *,
        filled_qty: Decimal | None = None,
        apply_position: bool = True,
    ) -> OrderSnapshot:
        order = self._orders_by_client[client_order_id]
        order_qty = order.qty if order.qty is not None else Decimal("0")
        qty = filled_qty if filled_qty is not None else order_qty
        status = "filled" if qty >= order_qty else "partially_filled"
        filled = order.model_copy(
            update={
                "status": status,
                "filled_qty": qty,
                "filled_avg_price": order.limit_price,
            }
        )
        self._orders_by_client[client_order_id] = filled
        self._orders_by_broker[order.broker_order_id] = filled
        if apply_position and order.side.lower() in {"buy", "buy_to_cover"} and qty > 0:
            self._apply_stock_fill(order.symbol, qty, order.limit_price or Decimal("0"))
        return filled

    def _apply_stock_fill(self, symbol: str, qty: Decimal, price: Decimal) -> None:
        for i, pos in enumerate(self._positions):
            if pos.symbol.upper() != symbol.upper():
                continue
            if (pos.asset_class or "us_equity").lower() not in {"us_equity", "equity", "stock"}:
                continue
            new_qty = pos.qty + qty
            # Weighted average entry
            if new_qty > 0:
                avg = ((pos.qty * pos.avg_entry_price) + (qty * price)) / new_qty
            else:
                avg = pos.avg_entry_price
            self._positions[i] = pos.model_copy(
                update={
                    "qty": new_qty,
                    "qty_available": new_qty,
                    "avg_entry_price": avg,
                    "market_value": new_qty * avg,
                }
            )
            self._cash -= qty * price
            return
        self._positions.append(
            PositionSnapshot(
                symbol=symbol.upper(),
                qty=qty,
                qty_available=qty,
                avg_entry_price=price,
                market_value=qty * price,
                side="long",
                asset_class="us_equity",
            )
        )
        self._cash -= qty * price
