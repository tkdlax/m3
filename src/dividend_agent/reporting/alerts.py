"""File + SQLite notifications (v1; no external chat required)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from dividend_agent.scheduling.calendar import now_utc


class AlertSink:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        reports_dir: Path | None = None,
    ) -> None:
        self._conn = conn
        self._reports_dir = reports_dir

    def emit(
        self,
        notification_type: str,
        title: str,
        *,
        body: str = "",
        severity: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> int:
        created = now_utc().replace(microsecond=0).isoformat()
        cur = self._conn.execute(
            """
            INSERT INTO notifications (
                notification_type, severity, title, body, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                notification_type,
                severity,
                title,
                body,
                json.dumps(payload or {}),
                created,
            ),
        )
        note_id = int(cur.lastrowid or 0)
        if self._reports_dir is not None:
            self._reports_dir.mkdir(parents=True, exist_ok=True)
            path = self._reports_dir / f"alert-{note_id}-{notification_type}.json"
            path.write_text(
                json.dumps(
                    {
                        "id": note_id,
                        "type": notification_type,
                        "severity": severity,
                        "title": title,
                        "body": body,
                        "payload": payload or {},
                        "created_at": created,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        return note_id

    def list_recent(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM notifications ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
