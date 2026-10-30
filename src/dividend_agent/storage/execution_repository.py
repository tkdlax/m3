"""Persist local orders, events, and approvals."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class ExecutionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_local_order(self, row: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO local_orders (
                client_order_id, intent_id, account_id, symbol, side, qty,
                order_type, limit_price, state, broker_order_id, broker_status,
                filled_qty, requires_approval, hard_block, rejection_reasons_json,
                intent_json, created_at, updated_at
            ) VALUES (
                :client_order_id, :intent_id, :account_id, :symbol, :side, :qty,
                :order_type, :limit_price, :state, :broker_order_id, :broker_status,
                :filled_qty, :requires_approval, :hard_block, :rejection_reasons_json,
                :intent_json, :created_at, :updated_at
            )
            ON CONFLICT(client_order_id) DO UPDATE SET
                state=excluded.state,
                broker_order_id=excluded.broker_order_id,
                broker_status=excluded.broker_status,
                filled_qty=excluded.filled_qty,
                requires_approval=excluded.requires_approval,
                hard_block=excluded.hard_block,
                rejection_reasons_json=excluded.rejection_reasons_json,
                updated_at=excluded.updated_at
            """,
            row,
        )

    def get_order(self, client_order_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM local_orders WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_orders(self, *, state: str | None = None) -> list[dict[str, Any]]:
        if state:
            rows = self._conn.execute(
                "SELECT * FROM local_orders WHERE state = ? ORDER BY created_at",
                (state,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM local_orders ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def append_event(
        self,
        client_order_id: str,
        event_type: str,
        *,
        from_state: str | None = None,
        to_state: str | None = None,
        detail: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO order_events (
                client_order_id, event_type, from_state, to_state, detail,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_order_id,
                event_type,
                from_state,
                to_state,
                detail,
                json.dumps(payload or {}),
                _now(),
            ),
        )

    def list_events(self, client_order_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM order_events WHERE client_order_id = ?
            ORDER BY id
            """,
            (client_order_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def insert_approval(self, row: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO approvals (
                approval_id, client_order_id, approval_type, status, reason,
                risk_details, snapshot_json, expires_at, decided_at, decided_by,
                created_at
            ) VALUES (
                :approval_id, :client_order_id, :approval_type, :status, :reason,
                :risk_details, :snapshot_json, :expires_at, :decided_at, :decided_by,
                :created_at
            )
            """,
            row,
        )

    def update_approval(self, approval_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [approval_id]
        self._conn.execute(
            f"UPDATE approvals SET {cols} WHERE approval_id = ?",
            values,
        )

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_approvals(self, *, status: str | None = "pending") -> list[dict[str, Any]]:
        if status is None:
            rows = self._conn.execute("SELECT * FROM approvals ORDER BY created_at DESC").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM approvals WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def pending_approval_for_order(self, client_order_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM approvals
            WHERE client_order_id = ? AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
            """,
            (client_order_id,),
        ).fetchone()
        return dict(row) if row else None
