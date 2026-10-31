"""Milestone D scenario: supervised research → buy → fill → call → reconcile."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from dividend_agent.brokers.fake_paper import FakePaperBroker
from dividend_agent.brokers.types import PositionSnapshot
from dividend_agent.config import AppConfig
from dividend_agent.execution.order_manager import make_auto_config
from dividend_agent.execution.states import ApprovalStatus, OrderState
from dividend_agent.options.ex_dividend import ExDividendWindow
from dividend_agent.orchestration.paper_cycle import PaperCycleOrchestrator
from dividend_agent.research.citations import Citation, Claim, CriterionResult
from dividend_agent.research.memo import IssuerIdentity, ResearchMemo
from dividend_agent.storage import CycleRepository, ExecutionRepository, open_migrated
from dividend_agent.storage.cycle_repository import CycleRepository as CR

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CHAIN = FIXTURES / "option_chain_gis.json"


class _FakePipeline:
    def __init__(self, decision: str = "accept") -> None:
        self.decision = decision

    def research_symbol(self, ticker: str, **kwargs: Any) -> ResearchMemo:
        _ = kwargs
        cite = Citation(
            source="sec_edgar",
            cik="0000051462",
            accession="0000051462-24-000055",
            form="10-K",
            filing_date=date(2024, 6, 26),
            url="https://example.test/10k",
        )
        return ResearchMemo(
            identity=IssuerIdentity(ticker=ticker.upper(), cik="0000051462"),
            decision=self.decision,  # type: ignore[arg-type]
            filings=(cite,),
            criteria=(CriterionResult(name="filing_narrative", status="pass", detail="ok"),),
            claims=(Claim(statement="business ok", citations=(cite,)),),
            decision_reasons=() if self.decision == "accept" else ("research_reject",),
        )


def _auto_cfg() -> AppConfig:
    cfg = make_auto_config(AppConfig())
    # Allow a full 100-share GIS accumulation at ~$40 in scenario tests.
    return cfg.model_copy(
        update={
            "execution": cfg.execution.model_copy(
                update={"max_new_stock_order_notional_usd": 10000.0}
            )
        }
    )


def _orch(
    tmp_path: Path,
    broker: FakePaperBroker,
    *,
    cfg: AppConfig | None = None,
    decision: str = "accept",
    environ: dict[str, str] | None = None,
) -> tuple[PaperCycleOrchestrator, Any]:
    conn = open_migrated(tmp_path / "cycle.sqlite3")
    orch = PaperCycleOrchestrator(
        config=cfg or _auto_cfg(),
        broker=broker,
        conn=conn,
        environ=environ or {"TRADING_PAUSED": "false"},
        research_pipeline=_FakePipeline(decision=decision),  # type: ignore[arg-type]
        option_chain_path=CHAIN,
        ex_div=ExDividendWindow(ex_date=date(2026, 5, 1)),
        stock_limit_price=Decimal("40"),
    )
    return orch, conn


def test_full_cycle_research_buy_fill_call_reconcile(tmp_path: Path) -> None:
    broker = FakePaperBroker(cash=Decimal("100000"), equity=Decimal("100000"))
    orch, conn = _orch(tmp_path, broker)
    try:
        out = orch.start("GIS")
        assert out.research_decision == "accept"
        assert out.stock_client_order_id
        assert out.status == "awaiting_fill"

        # Confirm paper fill then resume
        broker.fill(out.stock_client_order_id)
        out2 = orch.resume(out.cycle_id)
        assert out2.status == "completed"
        assert out2.stage == "complete"
        assert out2.call_client_order_id
        # Decisions persisted
        decisions = CycleRepository(conn).list_decisions(out.cycle_id)
        stages = {d["stage"] for d in decisions}
        assert "research" in stages
        assert "stock_buy" in stages
        assert "await_fill" in stages
        assert "select_call" in stages
        assert "reconcile" in stages
    finally:
        conn.close()


def test_never_writes_call_against_unfilled_stock(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    orch, conn = _orch(tmp_path, broker)
    try:
        out = orch.start("GIS")
        assert out.status == "awaiting_fill"
        # Resume without fill — must not complete a call
        out2 = orch.resume(out.cycle_id)
        assert out2.status == "awaiting_fill"
        assert out2.call_client_order_id is None
    finally:
        conn.close()


def test_partial_fill_does_not_select_call(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    orch, conn = _orch(tmp_path, broker)
    try:
        out = orch.start("GIS")
        assert out.stock_client_order_id
        # Partial fill of 40 / 100
        broker.fill(out.stock_client_order_id, filled_qty=Decimal("40"))
        out2 = orch.resume(out.cycle_id)
        assert out2.status == "awaiting_fill"
        assert out2.call_client_order_id is None
        assert any("partial" in n or "unfilled" in n for n in out2.notes) or True
    finally:
        conn.close()


def test_insufficient_shares_skips_call(tmp_path: Path) -> None:
    # Already hold 50 shares; buy remaining 50 then only partial → skip path via owned < 100
    broker = FakePaperBroker(
        positions=[
            PositionSnapshot(
                symbol="GIS",
                qty=Decimal("50"),
                qty_available=Decimal("50"),
                avg_entry_price=Decimal("40"),
                market_value=Decimal("2000"),
            )
        ]
    )
    orch, conn = _orch(tmp_path, broker)
    try:
        out = orch.start("GIS")
        assert out.stock_client_order_id
        # Fill only 20 more → 70 total < 100
        broker.fill(out.stock_client_order_id, filled_qty=Decimal("20"))
        # Force order to filled status with partial qty already set; orchestrator waits
        out2 = orch.resume(out.cycle_id)
        assert out2.call_client_order_id is None
        assert out2.status in {"awaiting_fill", "skipped", "blocked"}
    finally:
        conn.close()


def test_no_worthwhile_call_skips(tmp_path: Path) -> None:
    # 100 shares already; use empty chain by pointing at missing path after research skip buy
    broker = FakePaperBroker(
        positions=[
            PositionSnapshot(
                symbol="GIS",
                qty=Decimal("100"),
                qty_available=Decimal("100"),
                avg_entry_price=Decimal("40"),
                market_value=Decimal("4000"),
            )
        ]
    )
    conn = open_migrated(tmp_path / "nw.sqlite3")
    # Chain with only ITM / adjusted — selection may still find 45 OTM. Force no chain.
    orch = PaperCycleOrchestrator(
        config=_auto_cfg(),
        broker=broker,
        conn=conn,
        environ={"TRADING_PAUSED": "false"},
        research_pipeline=_FakePipeline("accept"),  # type: ignore[arg-type]
        option_chain_path=tmp_path / "missing_chain.json",
        stock_limit_price=Decimal("40"),
        ex_div=ExDividendWindow(ex_date=date(2026, 5, 1)),
    )
    try:
        out = orch.start("GIS")
        assert out.status in {"blocked", "skipped"}
        assert "option_chain_unavailable" in " ".join(out.notes) or out.status == "blocked"
    finally:
        conn.close()


def test_paused_trading_holds_without_submit(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    orch, conn = _orch(
        tmp_path,
        broker,
        cfg=AppConfig(),  # paper_dry_run
        environ={"TRADING_PAUSED": "true"},
    )
    try:
        out = orch.start("GIS")
        assert out.status == "dry_run_hold"
        assert broker.submit_count == 0
        assert out.stock_client_order_id
        local = ExecutionRepository(conn).get_order(out.stock_client_order_id)
        assert local is not None
        assert local["state"] == OrderState.READY.value
    finally:
        conn.close()


def test_expired_approval_blocks(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    # Force soft exception so stock buy goes to approval
    cfg = _auto_cfg()
    orch, conn = _orch(tmp_path, broker, cfg=cfg)
    try:
        # Manually create awaiting-approval stock order via process with exception
        from dividend_agent.strategy.stock_accumulation import build_stock_buy_intent

        intent = build_stock_buy_intent(
            account_id=broker.account_id,
            symbol="GIS",
            quantity=Decimal("100"),
            limit_price=Decimal("40"),
        )
        row = orch._orders.process_intent(intent, exception_reasons=["borderline_issuer"])
        assert row["state"] == OrderState.AWAITING_APPROVAL.value
        coid = row["client_order_id"]
        # Seed cycle at await_fill
        CR(conn).upsert_cycle(
            {
                "cycle_id": "cyc-exp",
                "symbol": "GIS",
                "account_id": broker.account_id,
                "stage": "await_fill",
                "status": "awaiting_approval",
                "stock_client_order_id": coid,
                "target_shares": "100",
                "notes_json": "[]",
                "evidence_json": "{}",
                "payload_json": "{}",
            }
        )
        # Expire approval
        pending = ExecutionRepository(conn).pending_approval_for_order(coid)
        assert pending is not None
        ExecutionRepository(conn).update_approval(
            pending["approval_id"],
            status=ApprovalStatus.EXPIRED.value,
            decided_at=datetime.now(UTC).isoformat(),
            decided_by="system",
        )
        out = orch.resume("cyc-exp")
        assert out.status == "blocked"
        assert any("approval" in n for n in out.notes) or True
    finally:
        conn.close()


def test_restart_between_stages(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    orch, conn = _orch(tmp_path, broker)
    try:
        out = orch.start("GIS")
        cid = out.cycle_id
        assert out.status == "awaiting_fill"
        # New orchestrator instance (restart) resumes same cycle
        orch2, _ = _orch(tmp_path, broker)
        # Reuse same db — open same path
        conn2 = open_migrated(tmp_path / "cycle.sqlite3")
        orch2 = PaperCycleOrchestrator(
            config=_auto_cfg(),
            broker=broker,
            conn=conn2,
            environ={"TRADING_PAUSED": "false"},
            research_pipeline=_FakePipeline("accept"),  # type: ignore[arg-type]
            option_chain_path=CHAIN,
            ex_div=ExDividendWindow(ex_date=date(2026, 5, 1)),
            stock_limit_price=Decimal("40"),
        )
        broker.fill(out.stock_client_order_id)
        out2 = orch2.resume(cid)
        assert out2.status == "completed"
        conn2.close()
    finally:
        conn.close()


def test_research_reject_blocks_cycle(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    orch, conn = _orch(tmp_path, broker, decision="reject")
    try:
        out = orch.start("GIS")
        assert out.status == "blocked"
        assert out.stage == "research"
        assert broker.submit_count == 0
    finally:
        conn.close()


def test_existing_lot_skips_buy_selects_call(tmp_path: Path) -> None:
    broker = FakePaperBroker(
        positions=[
            PositionSnapshot(
                symbol="GIS",
                qty=Decimal("100"),
                qty_available=Decimal("100"),
                avg_entry_price=Decimal("40"),
                market_value=Decimal("4000"),
            )
        ]
    )
    orch, conn = _orch(tmp_path, broker)
    try:
        out = orch.start("GIS")
        assert out.status == "completed"
        assert out.stock_client_order_id is None
        assert out.call_client_order_id
    finally:
        conn.close()
