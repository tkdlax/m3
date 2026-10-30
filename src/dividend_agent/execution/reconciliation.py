"""Pull broker truth into the local journal (read-only; no order submission)."""

from __future__ import annotations

from dataclasses import dataclass, field

from dividend_agent.brokers.interface import BrokerAdapter, require_verified_paper
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


class Reconciler:
    """Broker → SQLite snapshot sync for M1."""

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
            orders = self._broker.list_orders(status="all", limit=100)
            activities = self._broker.list_activities(page_size=100)

            self._repo.upsert_account(account)
            self._repo.replace_holdings(account.account_id, positions)
            self._repo.upsert_orders(account.account_id, orders)
            self._repo.upsert_activities(account.account_id, activities)

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
