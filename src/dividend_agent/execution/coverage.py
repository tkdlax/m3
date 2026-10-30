"""Independently derive coverage / WAC from trusted broker + local state."""

from __future__ import annotations

import re
from decimal import Decimal

from dividend_agent.brokers.interface import BrokerAdapter
from dividend_agent.brokers.types import OrderSnapshot, PositionSnapshot
from dividend_agent.domain.models import TradeIntent
from dividend_agent.portfolio.lots import CoverageState, ShareLot
from dividend_agent.storage.execution_repository import ExecutionRepository

# OCC root: leading letters before YYMMDD + C/P + strike.
_OCC_ROOT = re.compile(r"^([A-Z]{1,6})\d{6}[CP]\d{8}$")


def underlying_from_intent(intent: TradeIntent) -> str | None:
    """Resolve underlying equity symbol from intent extras or OCC option symbol."""
    extras = intent.extras or {}
    for key in ("underlying", "underlying_symbol"):
        raw = extras.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().upper()
    symbol = (intent.symbol or "").strip().upper()
    match = _OCC_ROOT.match(symbol)
    if match:
        return match.group(1)
    # Equity symbol (stock buy intents) — letters only, short.
    if symbol.isalpha() and 1 <= len(symbol) <= 6:
        return symbol
    return None


def _equity_lots(positions: list[PositionSnapshot], underlying: str) -> tuple[ShareLot, ...]:
    lots: list[ShareLot] = []
    for pos in positions:
        if pos.symbol.upper() != underlying.upper():
            continue
        if (pos.asset_class or "us_equity").lower() not in {"us_equity", "equity", "stock"}:
            continue
        if pos.side.lower() not in {"long", "buy"}:
            continue
        qty = pos.qty_available if pos.qty_available is not None else pos.qty
        if qty <= 0:
            continue
        lots.append(ShareLot(qty=qty, cost_per_share=pos.avg_entry_price))
    return tuple(lots)


def _short_call_shares_reserved(
    positions: list[PositionSnapshot],
    underlying: str,
) -> Decimal:
    """Shares encumbered by open short call positions on the underlying."""
    reserved = Decimal("0")
    for pos in positions:
        sym = pos.symbol.upper()
        match = _OCC_ROOT.match(sym)
        if not match or match.group(1) != underlying.upper():
            continue
        # Short options: side short / negative qty
        qty = abs(pos.qty)
        if pos.side.lower() in {"short", "sell"} or pos.qty < 0:
            # Standard deliverable 100; adjusted contracts rejected elsewhere.
            reserved += qty * Decimal("100")
    return reserved


def _pending_sell_shares(
    orders: list[OrderSnapshot],
    underlying: str,
) -> Decimal:
    """Shares reserved by open stock sell orders on the underlying."""
    reserved = Decimal("0")
    open_statuses = {
        "new",
        "accepted",
        "pending_new",
        "partially_filled",
        "held",
        "open",
    }
    for order in orders:
        if (order.symbol or "").upper() != underlying.upper():
            continue
        if (order.side or "").lower() not in {"sell", "sell_short"}:
            continue
        status = (order.status or "").lower()
        if status not in open_statuses:
            continue
        qty = order.qty or Decimal("0")
        filled = order.filled_qty or Decimal("0")
        remaining = qty - filled
        if remaining > 0:
            reserved += remaining
    return reserved


def derive_coverage(
    *,
    broker: BrokerAdapter,
    repo: ExecutionRepository,
    intent: TradeIntent,
    exclude_client_order_id: str | None = None,
) -> CoverageState | None:
    """Build CoverageState from broker holdings + local active reservations.

    Returns None when underlying cannot be resolved (caller must hard-block).
    Local reservations for ``exclude_client_order_id`` are omitted so revalidation
    of the same order does not double-count its own reservation.
    """
    underlying = underlying_from_intent(intent)
    if underlying is None:
        return None

    positions = broker.list_positions()
    orders = broker.list_orders(status="open", limit=500)
    lots = _equity_lots(positions, underlying)
    open_calls = _short_call_shares_reserved(positions, underlying)
    pending_sells = _pending_sell_shares(orders, underlying)

    local = Decimal(
        repo.active_reserved_shares(account_id=intent.account_id, underlying=underlying)
    )
    if exclude_client_order_id:
        own = repo.get_reservation(exclude_client_order_id)
        if own is not None and own.get("status") == "active":
            local -= Decimal(str(own["shares"]))
            if local < 0:
                local = Decimal("0")

    return CoverageState(
        symbol=underlying,
        lots=lots,
        shares_reserved_by_open_calls=open_calls,
        shares_reserved_by_pending_sells=pending_sells + local,
    )


def broker_unencumbered_before_local(
    *,
    broker: BrokerAdapter,
    intent: TradeIntent,
) -> Decimal | None:
    """Unencumbered shares from broker only (no local reservations)."""
    underlying = underlying_from_intent(intent)
    if underlying is None:
        return None
    positions = broker.list_positions()
    orders = broker.list_orders(status="open", limit=500)
    lots = _equity_lots(positions, underlying)
    owned = sum((lot.qty for lot in lots), Decimal("0"))
    reserved = _short_call_shares_reserved(positions, underlying) + _pending_sell_shares(
        orders, underlying
    )
    return owned - reserved
