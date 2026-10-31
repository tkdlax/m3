"""Persist supervised paper-cycle stage state and decisions."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class CycleRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_cycle(self, row: dict[str, Any]) -> None:
        now = _now()
        self._conn.execute(
            """
            INSERT INTO paper_cycles (
                cycle_id, symbol, account_id, stage, status, research_decision,
                memo_id, stock_client_order_id, call_client_order_id, target_shares,
                notes_json, evidence_json, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cycle_id) DO UPDATE SET
                stage=excluded.stage,
                status=excluded.status,
                research_decision=excluded.research_decision,
                memo_id=excluded.memo_id,
                stock_client_order_id=excluded.stock_client_order_id,
                call_client_order_id=excluded.call_client_order_id,
                target_shares=excluded.target_shares,
                notes_json=excluded.notes_json,
                evidence_json=excluded.evidence_json,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                row["cycle_id"],
                row["symbol"],
                row["account_id"],
                row["stage"],
                row["status"],
                row.get("research_decision"),
                row.get("memo_id"),
                row.get("stock_client_order_id"),
                row.get("call_client_order_id"),
                row.get("target_shares", "100"),
                row.get("notes_json", "[]"),
                row.get("evidence_json", "{}"),
                row.get("payload_json", "{}"),
                row.get("created_at", now),
                now,
            ),
        )

    def get_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM paper_cycles WHERE cycle_id = ?", (cycle_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_open_cycles(self, *, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            rows = self._conn.execute(
                """
                SELECT * FROM paper_cycles
                WHERE symbol = ? AND status IN ('in_progress', 'awaiting_fill', 'awaiting_approval')
                ORDER BY updated_at DESC
                """,
                (symbol.upper(),),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM paper_cycles
                WHERE status IN ('in_progress', 'awaiting_fill', 'awaiting_approval')
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def record_decision(
        self,
        *,
        cycle_id: str,
        stage: str,
        decision: str,
        reasons: list[str] | tuple[str, ...] = (),
        evidence: dict[str, Any] | None = None,
        outcome: str = "",
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO cycle_decisions (
                cycle_id, stage, decision, reasons_json, evidence_json, outcome, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cycle_id,
                stage,
                decision,
                json.dumps(list(reasons)),
                json.dumps(evidence or {}),
                outcome,
                _now(),
            ),
        )

    def list_decisions(self, cycle_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM cycle_decisions
            WHERE cycle_id = ?
            ORDER BY id ASC
            """,
            (cycle_id,),
        ).fetchall()
        return [dict(r) for r in rows]
