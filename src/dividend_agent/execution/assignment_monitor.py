"""Assignment / expiry monitoring hooks (Milestone E).

Detects broker corporate-action activities, persists outcomes, and emits
alerts. Does not invent share adjustments — records expected effects for
operator / reconciliation follow-up.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from dividend_agent.brokers.interface import BrokerAdapter
from dividend_agent.brokers.types import ActivitySnapshot

if TYPE_CHECKING:
    from dividend_agent.reporting.alerts import AlertSink

ASSIGNMENT_TYPES = frozenset({"OPASN", "ASSIGN", "EXERCISE"})
EXPIRY_TYPES = frozenset({"OPEXP", "EXP", "EXPIRATION"})
MONITORED_TYPES = ASSIGNMENT_TYPES | EXPIRY_TYPES


@dataclass
class AssignmentMonitorResult:
    scanned: int = 0
    new_events: int = 0
    assignments: int = 0
    expiries: int = 0
    event_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "new_events": self.new_events,
            "assignments": self.assignments,
            "expiries": self.expiries,
            "event_ids": self.event_ids,
            "notes": self.notes,
        }


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _effect_for(activity_type: str) -> str:
    upper = activity_type.upper()
    if upper in ASSIGNMENT_TYPES:
        return "shares_called_away_or_delivered — reconcile holdings vs reservations"
    if upper in EXPIRY_TYPES:
        return "short_call_expired — release coverage reservation expectation"
    return "corporate_action_review"


class AssignmentMonitor:
    """Scan broker activities and persist assignment/expiry outcomes."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        alerts: AlertSink | None = None,
    ) -> None:
        self._conn = conn
        self._alerts = alerts

    def scan_broker(
        self,
        broker: BrokerAdapter,
        *,
        account_id: str,
        page_size: int = 100,
    ) -> AssignmentMonitorResult:
        activities = broker.list_activities(page_size=page_size)
        monitored = [a for a in activities if (a.activity_type or "").upper() in MONITORED_TYPES]
        result = AssignmentMonitorResult(scanned=len(activities))
        cursor_key = f"activities:{account_id}"
        last = self._get_cursor(cursor_key)

        for activity in monitored:
            if not self._is_new(activity, last_cursor=last):
                continue
            event_id = self._persist(account_id, activity)
            if event_id is None:
                continue
            result.new_events += 1
            result.event_ids.append(event_id)
            kind = (activity.activity_type or "").upper()
            if kind in ASSIGNMENT_TYPES:
                result.assignments += 1
            else:
                result.expiries += 1
            if self._alerts is not None:
                self._alerts.emit(
                    "assignment_or_expiry",
                    f"{kind} detected for {activity.symbol or 'unknown'}",
                    body=_effect_for(kind),
                    severity="warning",
                    payload={
                        "event_id": event_id,
                        "activity_type": kind,
                        "symbol": activity.symbol,
                        "qty": str(activity.qty) if activity.qty is not None else None,
                    },
                )

        # Advance cursor to newest transaction time among all scanned activities
        newest = _newest_time(activities)
        if newest:
            self._set_cursor(cursor_key, newest)
            result.notes.append(f"cursor={newest}")
        return result

    def list_unhandled(self, *, account_id: str | None = None) -> list[dict[str, Any]]:
        if account_id:
            rows = self._conn.execute(
                """
                SELECT * FROM corporate_action_events
                WHERE account_id = ? AND handled = 0
                ORDER BY created_at DESC
                """,
                (account_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM corporate_action_events
                WHERE handled = 0
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_handled(self, event_id: str) -> None:
        self._conn.execute(
            """
            UPDATE corporate_action_events
            SET handled = 1, updated_at = ?
            WHERE event_id = ?
            """,
            (_now(), event_id),
        )

    def _persist(self, account_id: str, activity: ActivitySnapshot) -> str | None:
        broker_id = (activity.activity_id or "").strip()
        if not broker_id and activity.raw:
            broker_id = str(activity.raw.get("id") or activity.raw.get("activity_id") or "").strip()
        if not broker_id:
            # Stable synthetic id from type+symbol+time+qty
            broker_id = (
                f"{activity.activity_type}:{activity.symbol}:"
                f"{activity.transaction_time}:{activity.qty}"
            )
        existing = self._conn.execute(
            """
            SELECT event_id FROM corporate_action_events
            WHERE account_id = ? AND broker_activity_id = ?
            """,
            (account_id, broker_id),
        ).fetchone()
        if existing:
            return None
        event_id = f"cae-{uuid4().hex[:12]}"
        now = _now()
        kind = (activity.activity_type or "").upper()
        self._conn.execute(
            """
            INSERT INTO corporate_action_events (
                event_id, account_id, activity_type, symbol, qty, price, net_amount,
                transaction_time, broker_activity_id, effect, handled, payload_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                event_id,
                account_id,
                kind,
                activity.symbol,
                str(activity.qty) if activity.qty is not None else None,
                str(activity.price) if activity.price is not None else None,
                str(activity.net_amount) if activity.net_amount is not None else None,
                activity.transaction_time.isoformat() if activity.transaction_time else None,
                broker_id,
                _effect_for(kind),
                json.dumps(activity.raw or {}),
                now,
                now,
            ),
        )
        return event_id

    def _is_new(self, activity: ActivitySnapshot, *, last_cursor: str | None) -> bool:
        if last_cursor is None or activity.transaction_time is None:
            return True
        try:
            cursor_dt = datetime.fromisoformat(last_cursor)
        except ValueError:
            return True
        ts = activity.transaction_time
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if cursor_dt.tzinfo is None:
            cursor_dt = cursor_dt.replace(tzinfo=UTC)
        # Inclusive lower bound: persist() dedupes by broker_activity_id.
        return ts >= cursor_dt

    def _get_cursor(self, name: str) -> str | None:
        row = self._conn.execute(
            "SELECT cursor_value FROM ingestion_cursors WHERE cursor_name = ?",
            (name,),
        ).fetchone()
        return str(row["cursor_value"]) if row else None

    def _set_cursor(self, name: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO ingestion_cursors (cursor_name, cursor_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cursor_name) DO UPDATE SET
                cursor_value=excluded.cursor_value,
                updated_at=excluded.updated_at
            """,
            (name, value, _now()),
        )


def _newest_time(activities: list[ActivitySnapshot]) -> str | None:
    times = [a.transaction_time for a in activities if a.transaction_time is not None]
    if not times:
        return None
    newest = max(times)
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)
    return newest.isoformat()
