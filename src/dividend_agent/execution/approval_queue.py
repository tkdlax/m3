"""Durable human approval queue (spec §12). Hard blocks never approvable."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from dividend_agent.execution.states import HARD_BLOCK_REASONS, ApprovalStatus
from dividend_agent.storage.execution_repository import ExecutionRepository


class ApprovalError(RuntimeError):
    """Invalid approval operation."""


class ApprovalQueue:
    def __init__(self, repo: ExecutionRepository) -> None:
        self._repo = repo

    def enqueue(
        self,
        *,
        client_order_id: str,
        approval_type: str,
        reason: str,
        risk_details: str = "",
        snapshot: dict[str, Any] | None = None,
        ttl_hours: int = 24,
        hard_block_reasons: list[str] | None = None,
    ) -> str:
        hard = set(hard_block_reasons or []) & set(HARD_BLOCK_REASONS)
        if hard:
            raise ApprovalError(f"hard blocks cannot be queued for approval: {sorted(hard)}")
        # Supersede prior pending for same order
        existing = self._repo.pending_approval_for_order(client_order_id)
        if existing:
            self._repo.update_approval(
                existing["approval_id"],
                status=ApprovalStatus.SUPERSEDED.value,
                decided_at=_now(),
                decided_by="system",
            )
        approval_id = f"apr-{uuid4().hex[:16]}"
        expires = datetime.now(UTC) + timedelta(hours=ttl_hours)
        self._repo.insert_approval(
            {
                "approval_id": approval_id,
                "client_order_id": client_order_id,
                "approval_type": approval_type,
                "status": ApprovalStatus.PENDING.value,
                "reason": reason,
                "risk_details": risk_details,
                "snapshot_json": json.dumps(snapshot or {}),
                "expires_at": expires.replace(microsecond=0).isoformat(),
                "decided_at": None,
                "decided_by": None,
                "created_at": _now(),
            }
        )
        return approval_id

    def list_pending(self) -> list[dict[str, Any]]:
        self.expire_stale()
        return self._repo.list_approvals(status=ApprovalStatus.PENDING.value)

    def expire_stale(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        expired = 0
        for row in self._repo.list_approvals(status=ApprovalStatus.PENDING.value):
            exp = row.get("expires_at")
            if not exp:
                continue
            expires_at = datetime.fromisoformat(str(exp))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                self._repo.update_approval(
                    row["approval_id"],
                    status=ApprovalStatus.EXPIRED.value,
                    decided_at=_now(),
                    decided_by="system",
                )
                expired += 1
        return expired

    def approve(
        self,
        approval_id: str,
        *,
        decided_by: str = "operator",
    ) -> dict[str, Any]:
        self.expire_stale()
        row = self._repo.get_approval(approval_id)
        if row is None:
            raise ApprovalError(f"approval not found: {approval_id}")
        if row["status"] != ApprovalStatus.PENDING.value:
            raise ApprovalError(f"approval not pending: {row['status']}")
        snapshot = json.loads(row.get("snapshot_json") or "{}")
        hard = set(snapshot.get("hard_block_reasons") or []) & set(HARD_BLOCK_REASONS)
        if hard:
            raise ApprovalError(f"hard blocks can never be approved: {sorted(hard)}")
        self._repo.update_approval(
            approval_id,
            status=ApprovalStatus.APPROVED.value,
            decided_at=_now(),
            decided_by=decided_by,
        )
        updated = self._repo.get_approval(approval_id)
        assert updated is not None
        return updated

    def reject(self, approval_id: str, *, decided_by: str = "operator") -> dict[str, Any]:
        row = self._repo.get_approval(approval_id)
        if row is None:
            raise ApprovalError(f"approval not found: {approval_id}")
        if row["status"] != ApprovalStatus.PENDING.value:
            raise ApprovalError(f"approval not pending: {row['status']}")
        self._repo.update_approval(
            approval_id,
            status=ApprovalStatus.REJECTED.value,
            decided_at=_now(),
            decided_by=decided_by,
        )
        updated = self._repo.get_approval(approval_id)
        assert updated is not None
        return updated


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
