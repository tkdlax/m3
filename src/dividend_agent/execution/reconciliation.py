"""Pull broker truth into the local journal (read-only; no order submission)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from dividend_agent.brokers.interface import BrokerAdapter, require_verified_paper
from dividend_agent.brokers.types import ActivitySnapshot, OrderSnapshot
from dividend_agent.storage.repository import LedgerRepository


@dataclass
class ReconcileResult:
    account_id: str
    positions: int = 0
    orders: int = 0
    activities: int = 0
    job_id: int | None = None
    detail: str = ""
    mismatches: list[str] = field(default_factory=list)
    cursors: dict[str, str] = field(default_factory=dict)


class Reconciler:
    """Broker → SQLite snapshot sync with ingestion cursor retention (F)."""

    def __init__(self, broker: BrokerAdapter, repo: LedgerRepository) -> None:
        self._broker = broker
        self._repo = repo

    def reconcile(self) -> ReconcileResult:
        verification = self._broker.verify_paper()
        require_verified_paper(verification)
        account = self._broker.get_account()
        job_id = self._repo.start_job("reconcile", account.account_id)
        try:
            positions = self._broker.list_positions()
            # Adapters that paginate (Alpaca) return multiple pages; others one page.
            orders = self._broker.list_orders(status="all", limit=100)
            activities = self._broker.list_activities(page_size=100)

            self._repo.upsert_account(account)
            self._repo.replace_holdings(account.account_id, positions)
            self._repo.upsert_orders(account.account_id, orders)
            self._repo.upsert_activities(account.account_id, activities)

            cursors = self._retain_cursors(account.account_id, orders, activities)

            mismatches = self._detect_basic_mismatches(
                account.account_id, len(positions), len(orders), len(activities)
            )
            detail = (
                f"synced positions={len(positions)} orders={len(orders)} "
                f"activities={len(activities)}"
            )
            self._repo.append_audit(
                "reconcile.completed",
                {
                    "account_id": account.account_id,
                    "positions": len(positions),
                    "orders": len(orders),
                    "activities": len(activities),
                    "mismatches": mismatches,
                    "cursors": cursors,
                },
                account_id=account.account_id,
                correlation_id=str(job_id),
            )
            status = "completed" if not mismatches else "completed_with_warnings"
            self._repo.finish_job(job_id, status=status, detail=detail)
            return ReconcileResult(
                account_id=account.account_id,
                positions=len(positions),
                orders=len(orders),
                activities=len(activities),
                job_id=job_id,
                detail=detail,
                mismatches=mismatches,
                cursors=cursors,
            )
        except Exception as exc:
            self._repo.finish_job(job_id, status="failed", detail=str(exc))
            self._repo.append_audit(
                "reconcile.failed",
                {"error": str(exc)},
                account_id=verification.account_id,
                correlation_id=str(job_id),
            )
            raise

    def _retain_cursors(
        self,
        account_id: str,
        orders: Sequence[OrderSnapshot],
        activities: Sequence[ActivitySnapshot],
    ) -> dict[str, str]:
        """Persist newest order/activity markers into ingestion_cursors when present."""
        cursors: dict[str, str] = {}

        newest_activity: tuple[object, str] | None = None
        for a in activities:
            ts = a.transaction_time
            if ts is not None and (newest_activity is None or ts > newest_activity[0]):  # type: ignore[operator]
                newest_activity = (ts, a.activity_id or "")
        if newest_activity is not None:
            ts_obj, aid = newest_activity
            value = ts_obj.isoformat() if hasattr(ts_obj, "isoformat") else str(ts_obj)
            if aid:
                value = f"{value}|{aid}"
            key = f"reconcile:activities:{account_id}"
            self._repo.upsert_ingestion_cursor(key, value)
            cursors[key] = value

        newest_order: tuple[object, str] | None = None
        for o in orders:
            ts = o.submitted_at or o.updated_at
            if ts is not None and (newest_order is None or ts > newest_order[0]):  # type: ignore[operator]
                newest_order = (ts, o.broker_order_id or "")
        if newest_order is not None:
            ts_obj, oid = newest_order
            value = ts_obj.isoformat() if hasattr(ts_obj, "isoformat") else str(ts_obj)
            if oid:
                value = f"{value}|{oid}"
            key = f"reconcile:orders:{account_id}"
            self._repo.upsert_ingestion_cursor(key, value)
            cursors[key] = value
        return cursors

    def _detect_basic_mismatches(
        self,
        account_id: str,
        position_count: int,
        order_count: int,
        activity_count: int,
    ) -> list[str]:
        """Lightweight consistency checks after write."""
        mismatches: list[str] = []
        stored_positions = len(self._repo.list_holdings(account_id))
        if stored_positions != position_count:
            mismatches.append(
                f"holdings count {stored_positions} != broker positions {position_count}"
            )
        if self._repo.count_orders(account_id) < order_count:
            mismatches.append("orders table missing rows after upsert")
        if self._repo.count_activities(account_id) < activity_count:
            mismatches.append("activities table missing rows after upsert")
        return mismatches
