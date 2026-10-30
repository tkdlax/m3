"""Persist issuers, filings, research memos, and decisions."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from dividend_agent.research.citations import Citation
from dividend_agent.research.memo import ResearchMemo


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class ResearchRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_issuer(
        self,
        *,
        cik: str,
        ticker: str,
        entity_name: str | None = None,
        sector: str | None = None,
        optionable: bool | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO issuers (cik, ticker, entity_name, sector, optionable, raw_json, updated_at)
            VALUES (?, ?, ?, ?, ?, '{}', ?)
            ON CONFLICT(cik) DO UPDATE SET
                ticker=excluded.ticker,
                entity_name=excluded.entity_name,
                sector=excluded.sector,
                optionable=excluded.optionable,
                updated_at=excluded.updated_at
            """,
            (
                cik.zfill(10),
                ticker.upper(),
                entity_name,
                sector,
                None if optionable is None else int(optionable),
                _now(),
            ),
        )

    def upsert_filings(self, ticker: str, filings: list[Citation] | tuple[Citation, ...]) -> None:
        retrieved = _now()
        for f in filings:
            if not f.accession:
                continue
            self._conn.execute(
                """
                INSERT INTO filings (
                    accession, cik, ticker, form, filing_date, report_period,
                    url, raw_json, retrieved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(accession) DO UPDATE SET
                    form=excluded.form,
                    filing_date=excluded.filing_date,
                    report_period=excluded.report_period,
                    url=excluded.url,
                    retrieved_at=excluded.retrieved_at
                """,
                (
                    f.accession,
                    (f.cik or "").zfill(10),
                    ticker.upper(),
                    f.form or "",
                    f.filing_date.isoformat() if f.filing_date else None,
                    f.report_period.isoformat() if f.report_period else None,
                    f.url,
                    json.dumps(f.model_dump(mode="json")),
                    retrieved,
                ),
            )

    def save_memo(self, memo: ResearchMemo) -> None:
        self.upsert_issuer(
            cik=memo.identity.cik,
            ticker=memo.identity.ticker,
            entity_name=memo.identity.entity_name,
            sector=memo.identity.sector,
            optionable=memo.identity.optionable,
        )
        self.upsert_filings(memo.identity.ticker, memo.filings)
        self._conn.execute(
            """
            INSERT INTO research_memos (
                memo_id, ticker, cik, decision, thesis, unknowns_json,
                criteria_json, citations_json, memo_json, agent_provider, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memo_id) DO UPDATE SET
                decision=excluded.decision,
                thesis=excluded.thesis,
                memo_json=excluded.memo_json
            """,
            (
                str(memo.memo_id),
                memo.identity.ticker,
                memo.identity.cik,
                memo.decision,
                memo.thesis,
                json.dumps(list(memo.unknowns)),
                json.dumps([c.model_dump(mode="json") for c in memo.criteria]),
                json.dumps([c.model_dump(mode="json") for c in memo.citations]),
                memo.model_dump_json(),
                memo.agent_provider,
                memo.created_at.replace(microsecond=0).isoformat(),
            ),
        )
        self._conn.execute(
            """
            INSERT INTO research_decisions (ticker, memo_id, decision, reasons_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                memo.identity.ticker,
                str(memo.memo_id),
                memo.decision,
                json.dumps(list(memo.decision_reasons)),
                _now(),
            ),
        )

    def latest_memo(self, ticker: str) -> dict[str, object] | None:
        row = self._conn.execute(
            """
            SELECT * FROM research_memos WHERE ticker = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (ticker.upper(),),
        ).fetchone()
        return dict(row) if row else None
