"""Order lifecycle states (spec §13)."""

from __future__ import annotations

from enum import StrEnum


class OrderState(StrEnum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ERROR = "error"
    RECONCILIATION_NEEDED = "reconciliation_needed"


# Allowed transitions (from -> frozenset of to)
ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.PROPOSED: frozenset({OrderState.VALIDATED, OrderState.REJECTED, OrderState.ERROR}),
    OrderState.VALIDATED: frozenset(
        {
            OrderState.AWAITING_APPROVAL,
            OrderState.READY,
            OrderState.REJECTED,
            OrderState.ERROR,
        }
    ),
    OrderState.AWAITING_APPROVAL: frozenset(
        {
            OrderState.READY,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.ERROR,
        }
    ),
    OrderState.READY: frozenset({OrderState.SUBMITTED, OrderState.REJECTED, OrderState.ERROR}),
    OrderState.SUBMITTED: frozenset(
        {
            OrderState.ACKNOWLEDGED,
            OrderState.REJECTED,
            OrderState.ERROR,
            OrderState.RECONCILIATION_NEEDED,
        }
    ),
    OrderState.ACKNOWLEDGED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.RECONCILIATION_NEEDED,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.EXPIRED,
            OrderState.RECONCILIATION_NEEDED,
        }
    ),
    OrderState.RECONCILIATION_NEEDED: frozenset(
        {
            OrderState.ACKNOWLEDGED,
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.ERROR,
        }
    ),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.EXPIRED: frozenset(),
    OrderState.ERROR: frozenset({OrderState.RECONCILIATION_NEEDED}),
}


def can_transition(current: OrderState, new: OrderState) -> bool:
    if current == new:
        return True
    return new in ALLOWED_TRANSITIONS.get(current, frozenset())


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


# Hard blocks that can never be approved (§4 / §12).
HARD_BLOCK_REASONS: frozenset[str] = frozenset(
    {
        "live_destination",
        "uncovered_short_call",
        "strike_below_wac",
        "adjusted_contract_unsupported",
        "nonstandard_deliverable_unsupported",
        "insufficient_unencumbered_shares",
        "stale_quote",
        "missing_bid_premium",
        "paper_not_verified",
        "trading_paused",
        "live_trading_forbidden",
    }
)
