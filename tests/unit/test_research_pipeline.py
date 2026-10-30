"""Research pipeline + persistence with mocked SEC HTTP."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx

from dividend_agent.config import AppConfig
from dividend_agent.research.pipeline import ResearchPipeline
from dividend_agent.research.screener import MarketSnapshotInput
from dividend_agent.research.sec_edgar import SecEdgarClient
from dividend_agent.storage import ResearchRepository, open_migrated

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _sec_client() -> SecEdgarClient:
    tickers = json.loads((FIXTURES / "sec_company_tickers.json").read_text())
    submissions = json.loads((FIXTURES / "sec_submissions_gis.json").read_text())
    facts = json.loads((FIXTURES / "sec_companyfacts_gis.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/files/company_tickers.json"):
            return httpx.Response(200, json=tickers)
        if "submissions/CIK0000051462.json" in url:
            return httpx.Response(200, json=submissions)
        if "companyfacts/CIK0000051462.json" in url:
            return httpx.Response(200, json=facts)
        return httpx.Response(404)

    return SecEdgarClient(
        "DividendAgent/0.1 (tests@example.com)",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_pipeline_produces_cited_memo(tmp_path: Path) -> None:
    cfg = AppConfig()
    cfg = cfg.model_copy(
        update={
            "research": cfg.research.model_copy(
                update={"sec_user_agent": "DividendAgent/0.1 (tests@example.com)"}
            )
        }
    )
    pipeline = ResearchPipeline.from_config(cfg, sec_client=_sec_client())
    memo = pipeline.research_symbol(
        "GIS",
        market=MarketSnapshotInput(
            price=Decimal("40"),
            week52_high=Decimal("50"),
            week52_low=Decimal("30"),
            avg_dollar_volume=Decimal("50000000"),
            optionable=True,
        ),
    )
    assert memo.identity.cik == "0000051462"
    assert memo.filings
    assert memo.filings[0].accession == "0000051462-24-000055"
    assert memo.citations
    assert memo.decision in {"accept", "reject", "review"}

    conn = open_migrated(tmp_path / "research.sqlite3")
    try:
        ResearchRepository(conn).save_memo(memo)
        row = ResearchRepository(conn).latest_memo("GIS")
        assert row is not None
        assert row["decision"] == memo.decision
    finally:
        conn.close()


def test_migration_includes_research_tables(tmp_path: Path) -> None:
    conn = open_migrated(tmp_path / "r.sqlite3")
    try:
        from dividend_agent.storage.db import current_schema_version

        assert current_schema_version(conn) >= 2
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "research_memos" in tables
        assert "filings" in tables
        assert "issuers" in tables
    finally:
        conn.close()
