"""SQLite migration and repository tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from dividend_agent.brokers.types import AccountSnapshot, PositionSnapshot
from dividend_agent.storage import LedgerRepository, open_migrated
from dividend_agent.storage.db import current_schema_version


def test_migrations_and_wal(tmp_path: Path) -> None:
    db = tmp_path / "agent.sqlite3"
    conn = open_migrated(db)
    try:
        assert current_schema_version(conn) >= 1
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert str(mode).lower() == "wal"
    finally:
        conn.close()


def test_repository_roundtrip(tmp_path: Path) -> None:
    conn = open_migrated(tmp_path / "ledger.sqlite3")
    try:
        repo = LedgerRepository(conn)
        account = AccountSnapshot(
            account_id="paper-acct-001",
            status="ACTIVE",
            cash=Decimal("1000"),
            buying_power=Decimal("2000"),
            equity=Decimal("1500"),
            portfolio_value=Decimal("1500"),
        )
        repo.upsert_account(account)
        repo.replace_holdings(
            account.account_id,
            [
                PositionSnapshot(
                    symbol="GIS",
                    qty=Decimal("100"),
                    avg_entry_price=Decimal("45.10"),
                )
            ],
        )
        holdings = repo.list_holdings(account.account_id)
        assert len(holdings) == 1
        assert holdings[0].symbol == "GIS"
        job_id = repo.start_job("reconcile", account.account_id)
        repo.finish_job(job_id, status="completed", detail="ok")
        job = repo.latest_job("reconcile")
        assert job is not None
        assert job.status == "completed"
        repo.append_audit("test", {"ok": True}, account_id=account.account_id)
    finally:
        conn.close()
