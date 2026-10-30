"""Order authority isolation + atomic scheduler lease tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from dividend_agent.domain.models import TradeIntent
from dividend_agent.execution.authority import (
    AGENT_FORBIDDEN_BROKER_WRITES,
    OrderAuthorityError,
    assert_agent_has_no_write_credentials,
    assert_capabilities_are_read_only,
    decision_client_order_id,
    decision_fingerprint,
)
from dividend_agent.scheduling.locks import LockBusyError, SchedulerLock
from dividend_agent.storage import open_migrated


def test_agent_environ_rejects_write_secrets() -> None:
    with pytest.raises(OrderAuthorityError):
        assert_agent_has_no_write_credentials({"ALPACA_PAPER_API_KEY": "PK"})
    assert_agent_has_no_write_credentials({"TRADING_PAUSED": "true"})


def test_agent_capabilities_read_only() -> None:
    assert_capabilities_are_read_only(("get_account", "list_positions"))
    with pytest.raises(OrderAuthorityError):
        assert_capabilities_are_read_only(("get_account", "submit_paper_order"))
    assert "submit_paper_order" in AGENT_FORBIDDEN_BROKER_WRITES


def test_decision_fingerprint_stable_across_uuids() -> None:
    a = TradeIntent(
        account_id="a",
        symbol="GIS260401C00045000",
        side="sell",
        quantity=Decimal("1"),
        order_type="limit",
        limit_price=Decimal("0.55"),
        strike=Decimal("45"),
        deliverable_shares=100,
        rule_config_version="v1",
        intent_id=uuid4(),
        extras={"underlying": "GIS"},
    )
    b = a.model_copy(update={"intent_id": uuid4()})
    assert decision_fingerprint(a) == decision_fingerprint(b)
    assert decision_client_order_id(a) == decision_client_order_id(b)


def test_atomic_lease_acquire_and_renew(tmp_path: Path) -> None:
    conn = open_migrated(tmp_path / "lock.sqlite3")
    a = SchedulerLock(conn, holder="a", ttl_seconds=600)
    b = SchedulerLock(conn, holder="b", ttl_seconds=600)
    a.acquire()
    with pytest.raises(LockBusyError):
        b.acquire()
    a.renew()
    with pytest.raises(LockBusyError):
        b.acquire()
    a.release()
    b.acquire()
    b.renew()
    b.release()
    conn.close()


def test_expired_lease_can_be_stolen(tmp_path: Path) -> None:
    conn = open_migrated(tmp_path / "lock2.sqlite3")
    a = SchedulerLock(conn, holder="a", ttl_seconds=0)
    a.acquire()
    # ttl_seconds=0 => expires_at == acquired_at; steal when expires_at <= now
    b = SchedulerLock(conn, holder="b", ttl_seconds=60)
    b.acquire()
    b.release()
    conn.close()
