"""Supervised paper cycle: research → buy → confirmed fill → call → reconcile.

Never writes covered calls against unfilled stock purchases. Stages persist so
restarts can resume. Defaults remain ``paper_dry_run`` + paused (no auto submit).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from dividend_agent.brokers.interface import BrokerAdapter
from dividend_agent.config import AppConfig
from dividend_agent.execution.approval_queue import ApprovalQueue
from dividend_agent.execution.coverage import derive_coverage
from dividend_agent.execution.kill_switch import is_trading_paused
from dividend_agent.execution.order_manager import OrderManager
from dividend_agent.execution.reconciliation import Reconciler
from dividend_agent.execution.states import OrderState
from dividend_agent.market_data.interface import MarketDataAdapter
from dividend_agent.options.chains import OptionChain, load_chain_json
from dividend_agent.options.ex_dividend import ExDividendWindow
from dividend_agent.portfolio.lots import CoverageState
from dividend_agent.research.memo import ResearchMemo
from dividend_agent.research.pipeline import ResearchPipeline
from dividend_agent.research.screener import MarketSnapshotInput
from dividend_agent.storage.cycle_repository import CycleRepository
from dividend_agent.storage.execution_repository import ExecutionRepository
from dividend_agent.storage.research_repository import ResearchRepository
from dividend_agent.strategy.candidate_selection import select_covered_call_intents
from dividend_agent.strategy.stock_accumulation import (
    build_stock_buy_intent,
    shares_needed_for_lot,
)


class CycleStage(StrEnum):
    RESEARCH = "research"
    STOCK_BUY = "stock_buy"
    AWAIT_FILL = "await_fill"
    SELECT_CALL = "select_call"
    PROCESS_CALL = "process_call"
    RECONCILE = "reconcile"
    COMPLETE = "complete"


@dataclass
class CycleOutcome:
    cycle_id: str
    symbol: str
    stage: str
    status: str
    notes: list[str] = field(default_factory=list)
    research_decision: str | None = None
    stock_client_order_id: str | None = None
    call_client_order_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "symbol": self.symbol,
            "stage": self.stage,
            "status": self.status,
            "notes": self.notes,
            "research_decision": self.research_decision,
            "stock_client_order_id": self.stock_client_order_id,
            "call_client_order_id": self.call_client_order_id,
            "payload": self.payload,
        }


class PaperCycleOrchestrator:
    """Restart-safe supervised cycle. Missing hard safety inputs stay blocked."""

    def __init__(
        self,
        *,
        config: AppConfig,
        broker: BrokerAdapter,
        conn: sqlite3.Connection,
        environ: dict[str, str] | None = None,
        market_data: MarketDataAdapter | None = None,
        research_pipeline: ResearchPipeline | None = None,
        option_chain_path: Path | None = None,
        option_chain: OptionChain | None = None,
        ex_div: ExDividendWindow | None = None,
        target_shares: int = 100,
        stock_limit_price: Decimal | None = None,
    ) -> None:
        self._config = config
        self._broker = broker
        self._conn = conn
        self._environ = environ
        self._market_data = market_data
        self._pipeline = research_pipeline
        self._option_chain_path = option_chain_path
        self._option_chain = option_chain
        self._ex_div = ex_div
        self._target_shares = target_shares
        self._stock_limit_price = stock_limit_price
        self._cycles = CycleRepository(conn)
        self._exec = ExecutionRepository(conn)
        self._research = ResearchRepository(conn)
        self._approvals = ApprovalQueue(self._exec)
        self._orders = OrderManager(
            config=config,
            broker=broker,
            repo=self._exec,
            approvals=self._approvals,
            environ=environ,
        )
        self._ledger = None
        from dividend_agent.storage.repository import LedgerRepository

        self._ledger = LedgerRepository(conn)

    def start(
        self,
        symbol: str,
        *,
        cycle_id: str | None = None,
        account_id: str | None = None,
    ) -> CycleOutcome:
        verification = self._broker.verify_paper()
        acct = account_id or verification.account_id or "paper"
        cid = cycle_id or f"cyc-{uuid4().hex[:12]}"
        row = {
            "cycle_id": cid,
            "symbol": symbol.upper(),
            "account_id": acct,
            "stage": CycleStage.RESEARCH.value,
            "status": "in_progress",
            "target_shares": str(self._target_shares),
            "notes_json": "[]",
            "evidence_json": "{}",
            "payload_json": "{}",
        }
        self._cycles.upsert_cycle(row)
        self._record(cid, CycleStage.RESEARCH, "started", reasons=["cycle_created"])
        return self.advance(cid)

    def resume(self, cycle_id: str) -> CycleOutcome:
        row = self._cycles.get_cycle(cycle_id)
        if row is None:
            raise KeyError(f"unknown cycle {cycle_id}")
        return self.advance(cycle_id)

    def advance(self, cycle_id: str) -> CycleOutcome:
        """Advance one or more stages until blocked, waiting, or complete."""
        while True:
            row = self._cycles.get_cycle(cycle_id)
            if row is None:
                raise KeyError(f"unknown cycle {cycle_id}")
            status = row["status"]
            if status in {"completed", "blocked", "skipped"}:
                return self._outcome(row)
            stage = CycleStage(row["stage"])
            if stage == CycleStage.RESEARCH:
                self._stage_research(row)
            elif stage == CycleStage.STOCK_BUY:
                self._stage_stock_buy(row)
            elif stage == CycleStage.AWAIT_FILL:
                self._stage_await_fill(row)
            elif stage == CycleStage.SELECT_CALL:
                self._stage_select_call(row)
            elif stage == CycleStage.PROCESS_CALL:
                self._stage_process_call(row)
            elif stage == CycleStage.RECONCILE:
                self._stage_reconcile(row)
            else:
                break
            refreshed = self._cycles.get_cycle(cycle_id)
            assert refreshed is not None
            if refreshed["status"] in {
                "completed",
                "blocked",
                "skipped",
                "awaiting_fill",
                "awaiting_approval",
                "dry_run_hold",
            }:
                return self._outcome(refreshed)
            if refreshed["stage"] == row["stage"] and refreshed["status"] == row["status"]:
                # No progress
                return self._outcome(refreshed)
        return self._outcome(self._cycles.get_cycle(cycle_id) or row)

    # --- stages ---

    def _stage_research(self, row: dict[str, Any]) -> None:
        symbol = row["symbol"]
        notes = list(json.loads(row.get("notes_json") or "[]"))
        if self._pipeline is None:
            self._finish(
                row,
                stage=CycleStage.RESEARCH,
                status="blocked",
                decision="reject",
                reasons=["research_pipeline_unavailable"],
                notes=notes + ["missing research pipeline — fail closed"],
            )
            return
        market = MarketSnapshotInput()
        if self._market_data is not None:
            try:
                snap = self._market_data.get_stock_snapshot(symbol)
                market = MarketSnapshotInput(
                    price=snap.price,
                    week52_high=snap.week52_high,
                    week52_low=snap.week52_low,
                    avg_dollar_volume=snap.avg_dollar_volume,
                    optionable=snap.optionable if snap.optionable is not None else True,
                    price_as_of=snap.observed_at.date() if snap.observed_at else None,
                )
            except Exception as exc:  # noqa: BLE001
                notes.append(f"market_snapshot_unavailable:{exc}")
        memo: ResearchMemo = self._pipeline.research_symbol(symbol, market=market)
        self._research.save_memo(memo)
        evidence = {
            "memo_id": str(memo.memo_id),
            "decision": memo.decision,
            "decision_reasons": list(memo.decision_reasons),
            "unknowns": list(memo.unknowns),
            "citations": [c.display() for c in memo.citations[:10]],
        }
        self._record(
            row["cycle_id"],
            CycleStage.RESEARCH,
            memo.decision,
            reasons=list(memo.decision_reasons) or [memo.decision],
            evidence=evidence,
            outcome=memo.decision,
        )
        if memo.decision == "reject":
            self._finish(
                row,
                stage=CycleStage.RESEARCH,
                status="blocked",
                decision="reject",
                reasons=list(memo.decision_reasons) or ["research_reject"],
                notes=notes + ["research_rejected"],
                memo_id=str(memo.memo_id),
                evidence=evidence,
            )
            return
        # Accept or review → continue to stock accumulation (review still may buy under dry_run)
        next_status = "in_progress"
        self._update(
            row,
            stage=CycleStage.STOCK_BUY.value,
            status=next_status,
            research_decision=memo.decision,
            memo_id=str(memo.memo_id),
            notes=notes,
            evidence=evidence,
        )

    def _stage_stock_buy(self, row: dict[str, Any]) -> None:
        symbol = row["symbol"]
        account_id = row["account_id"]
        notes = list(json.loads(row.get("notes_json") or "[]"))
        target = int(Decimal(str(row.get("target_shares") or self._target_shares)))
        paused = is_trading_paused(environ=self._environ)

        # Pending unfilled buy for this cycle? Move to await_fill.
        if row.get("stock_client_order_id"):
            self._update(
                row,
                stage=CycleStage.AWAIT_FILL.value,
                status="awaiting_fill",
                notes=notes,
            )
            return

        coverage = self._coverage_for_symbol(symbol, account_id)
        need = shares_needed_for_lot(coverage, target_shares=target)
        if need <= 0:
            notes.append("already_at_target_shares")
            self._record(
                row["cycle_id"],
                CycleStage.STOCK_BUY,
                "skip_buy",
                reasons=["already_at_target_shares"],
                outcome="skip",
            )
            self._update(
                row,
                stage=CycleStage.SELECT_CALL.value,
                status="in_progress",
                notes=notes,
            )
            return

        price = self._stock_limit_price
        if price is None and self._market_data is not None:
            try:
                price = self._market_data.get_stock_snapshot(symbol).price
            except Exception:  # noqa: BLE001
                price = None
        if price is None:
            self._finish(
                row,
                stage=CycleStage.STOCK_BUY,
                status="blocked",
                decision="reject",
                reasons=["missing_stock_limit_price"],
                notes=notes + ["cannot size stock buy without price"],
            )
            return

        intent = build_stock_buy_intent(
            account_id=account_id,
            symbol=symbol,
            quantity=need,
            limit_price=price,
            evidence_refs=(row.get("memo_id") or "",),
            extras={"cycle_id": row["cycle_id"], "target_shares": target},
        )
        order_row = self._orders.process_intent(intent)
        coid = order_row["client_order_id"]
        state = order_row["state"]
        self._record(
            row["cycle_id"],
            CycleStage.STOCK_BUY,
            "proposed",
            reasons=[f"state={state}", f"qty={need}"],
            evidence={"client_order_id": coid, "limit_price": str(price)},
            outcome=state,
        )

        if state == OrderState.REJECTED.value:
            reasons = json.loads(order_row.get("rejection_reasons_json") or "[]")
            self._finish(
                row,
                stage=CycleStage.STOCK_BUY,
                status="blocked",
                decision="reject",
                reasons=reasons or ["stock_buy_rejected"],
                notes=notes + ["stock_buy_hard_blocked"],
                stock_coid=coid,
            )
            return

        if state == OrderState.AWAITING_APPROVAL.value:
            self._update(
                row,
                stage=CycleStage.AWAIT_FILL.value,
                status="awaiting_approval",
                stock_client_order_id=coid,
                notes=notes + ["stock_buy_routed_to_approval"],
            )
            return

        if state == OrderState.READY.value:
            # dry_run / paused: hold without inventing fills
            if paused or self._config.mode.value != "paper_auto":
                self._update(
                    row,
                    stage=CycleStage.AWAIT_FILL.value,
                    status="dry_run_hold",
                    stock_client_order_id=coid,
                    notes=notes
                    + [
                        "stock_buy_ready_not_submitted",
                        f"mode={self._config.mode.value}",
                        f"paused={paused}",
                    ],
                )
                return
            submitted = self._orders.submit_ready_order(coid)
            self._update(
                row,
                stage=CycleStage.AWAIT_FILL.value,
                status="awaiting_fill",
                stock_client_order_id=submitted["client_order_id"],
                notes=notes + [f"stock_submitted:{submitted['state']}"],
            )
            return

        # Already submitted / acknowledged
        self._update(
            row,
            stage=CycleStage.AWAIT_FILL.value,
            status="awaiting_fill",
            stock_client_order_id=coid,
            notes=notes,
        )

    def _stage_await_fill(self, row: dict[str, Any]) -> None:
        notes = list(json.loads(row.get("notes_json") or "[]"))
        coid = row.get("stock_client_order_id")
        if not coid:
            # No buy pending — proceed
            self._update(
                row,
                stage=CycleStage.SELECT_CALL.value,
                status="in_progress",
                notes=notes + ["no_stock_order_to_await"],
            )
            return

        # Expire stale approvals first
        expired = self._approvals.expire_stale()
        if expired:
            notes.append(f"approvals_expired={expired}")

        local = self._exec.get_order(coid)
        if local and local["state"] == OrderState.AWAITING_APPROVAL.value:
            pending = self._exec.pending_approval_for_order(coid)
            if pending is None:
                # Expired or missing — hard stop (cannot invent approval)
                self._finish(
                    row,
                    stage=CycleStage.AWAIT_FILL,
                    status="blocked",
                    decision="reject",
                    reasons=["approval_expired_or_missing"],
                    notes=notes + ["cannot_proceed_without_approval"],
                    stock_coid=coid,
                )
                return
            self._update(
                row,
                stage=CycleStage.AWAIT_FILL.value,
                status="awaiting_approval",
                notes=notes,
                stock_client_order_id=coid,
            )
            return

        if local and local["state"] == OrderState.READY.value:
            # Still dry-run hold
            self._update(
                row,
                stage=CycleStage.AWAIT_FILL.value,
                status="dry_run_hold",
                notes=notes,
                stock_client_order_id=coid,
            )
            return

        refreshed = self._orders.refresh_from_broker(coid)
        state = refreshed["state"]
        filled_qty = Decimal(str(refreshed.get("filled_qty") or "0"))
        intent_qty = Decimal(str(refreshed.get("qty") or "0"))

        if state == OrderState.FILLED.value and filled_qty >= intent_qty:
            self._record(
                row["cycle_id"],
                CycleStage.AWAIT_FILL,
                "filled",
                reasons=[f"filled_qty={filled_qty}"],
                evidence={"client_order_id": coid},
                outcome="filled",
            )
            self._update(
                row,
                stage=CycleStage.SELECT_CALL.value,
                status="in_progress",
                notes=notes + ["stock_fill_confirmed"],
                stock_client_order_id=coid,
            )
            return

        if state == OrderState.PARTIALLY_FILLED.value:
            notes.append(f"partial_fill:{filled_qty}/{intent_qty}")
            self._record(
                row["cycle_id"],
                CycleStage.AWAIT_FILL,
                "partial",
                reasons=[f"partial_fill:{filled_qty}/{intent_qty}"],
                evidence={"client_order_id": coid, "filled_qty": str(filled_qty)},
                outcome="partial",
            )
            # Do not write calls against incomplete fill — wait or evaluate shares
            coverage = self._coverage_for_symbol(row["symbol"], row["account_id"])
            owned = coverage.shares_owned if coverage else Decimal("0")
            if owned >= Decimal(str(row.get("target_shares") or 100)):
                # Enough shares from prior holdings + partial — still require no pending
                # unfilled BUY that would double-count. Partial means buy not done;
                # only proceed if owned already met target BEFORE relying on this fill.
                pass
            self._update(
                row,
                stage=CycleStage.AWAIT_FILL.value,
                status="awaiting_fill",
                notes=notes + ["never_write_calls_against_unfilled_stock"],
                stock_client_order_id=coid,
            )
            return

        if state in {
            OrderState.REJECTED.value,
            OrderState.CANCELED.value,
            OrderState.EXPIRED.value,
            OrderState.ERROR.value,
        }:
            self._finish(
                row,
                stage=CycleStage.AWAIT_FILL,
                status="blocked",
                decision="reject",
                reasons=[f"stock_order_{state}"],
                notes=notes,
                stock_coid=coid,
            )
            return

        self._update(
            row,
            stage=CycleStage.AWAIT_FILL.value,
            status="awaiting_fill",
            notes=notes + [f"still_{state}"],
            stock_client_order_id=coid,
        )

    def _stage_select_call(self, row: dict[str, Any]) -> None:
        symbol = row["symbol"]
        account_id = row["account_id"]
        notes = list(json.loads(row.get("notes_json") or "[]"))

        # Hard rule: pending unfilled stock buy blocks call writing
        stock_coid = row.get("stock_client_order_id")
        stock_row = self._exec.get_order(stock_coid) if stock_coid else None
        if stock_row and stock_row["state"] not in {
            OrderState.FILLED.value,
            OrderState.CANCELED.value,
            OrderState.REJECTED.value,
        }:
            # READY dry-run / submitted / partial — never write calls yet
            notes.append("blocked_call_unfilled_stock")
            self._record(
                row["cycle_id"],
                CycleStage.SELECT_CALL,
                "blocked",
                reasons=["unfilled_stock_purchase"],
                evidence={"stock_client_order_id": stock_coid, "state": stock_row["state"]},
            )
            status = (
                "dry_run_hold" if stock_row["state"] == OrderState.READY.value else "awaiting_fill"
            )
            self._update(
                row,
                stage=CycleStage.AWAIT_FILL.value,
                status=status,
                notes=notes,
                stock_client_order_id=stock_coid,
            )
            return

        coverage = self._coverage_for_symbol(symbol, account_id)
        if coverage is None or coverage.unencumbered_shares < Decimal("100"):
            self._finish(
                row,
                stage=CycleStage.SELECT_CALL,
                status="skipped",
                decision="skip",
                reasons=["insufficient_shares_for_covered_call"],
                notes=notes
                + [
                    f"unencumbered={coverage.unencumbered_shares if coverage else 0}",
                ],
                stock_coid=stock_coid,
            )
            return

        chain = self._load_chain(symbol)
        if chain is None:
            self._finish(
                row,
                stage=CycleStage.SELECT_CALL,
                status="blocked",
                decision="reject",
                reasons=["option_chain_unavailable"],
                notes=notes + ["no chain for selection"],
                stock_coid=stock_coid,
            )
            return

        as_of = chain.as_of or date.today()
        now = datetime.now(UTC)
        if chain.as_of:
            now = datetime(as_of.year, as_of.month, as_of.day, 15, 0, tzinfo=UTC)
        result = select_covered_call_intents(
            config=self._config,
            chain=chain,
            coverage=coverage,
            account_id=account_id,
            as_of=as_of,
            now=now,
            ex_div=self._ex_div,
        )
        self._record(
            row["cycle_id"],
            CycleStage.SELECT_CALL,
            "selected" if result.intents else "no_worthwhile_call",
            reasons=list(result.skipped)[:20] or ["no_intents"],
            evidence={
                "intents": len(result.intents),
                "alternatives": len(result.alternatives),
                "allocation_notes": list(result.allocation_notes),
            },
            outcome=str(len(result.intents)),
        )
        if not result.intents:
            self._finish(
                row,
                stage=CycleStage.SELECT_CALL,
                status="skipped",
                decision="skip",
                reasons=["no_worthwhile_call"],
                notes=notes + list(result.skipped)[:5],
                stock_coid=stock_coid,
                payload={"selection": {"skipped": list(result.skipped)[:20]}},
            )
            return

        # Persist first executable intent id into payload for process stage
        intent = result.intents[0]
        payload = json.loads(row.get("payload_json") or "{}")
        payload["call_intent_json"] = intent.model_dump(mode="json")
        self._update(
            row,
            stage=CycleStage.PROCESS_CALL.value,
            status="in_progress",
            notes=notes + [f"selected:{intent.symbol}"],
            stock_client_order_id=stock_coid,
            payload=payload,
        )

    def _stage_process_call(self, row: dict[str, Any]) -> None:
        from dividend_agent.domain.models import TradeIntent

        notes = list(json.loads(row.get("notes_json") or "[]"))
        payload = json.loads(row.get("payload_json") or "{}")
        intent_data = payload.get("call_intent_json")
        if not intent_data:
            self._finish(
                row,
                stage=CycleStage.PROCESS_CALL,
                status="blocked",
                decision="reject",
                reasons=["missing_call_intent"],
                notes=notes,
            )
            return

        # Re-check unfilled stock before gateway
        stock_coid = row.get("stock_client_order_id")
        stock_row = self._exec.get_order(stock_coid) if stock_coid else None
        if (
            stock_row
            and stock_row["state"] != OrderState.FILLED.value
            and stock_row["state"] not in {OrderState.CANCELED.value, OrderState.REJECTED.value}
        ):
            self._update(
                row,
                stage=CycleStage.AWAIT_FILL.value,
                status="awaiting_fill",
                notes=notes + ["process_call_blocked_unfilled_stock"],
                stock_client_order_id=stock_coid,
            )
            return

        intent = TradeIntent.model_validate(intent_data)
        coverage = self._coverage_for_symbol(row["symbol"], row["account_id"])
        order_row = self._orders.process_intent(intent, coverage=coverage)
        coid = order_row["client_order_id"]
        state = order_row["state"]
        self._record(
            row["cycle_id"],
            CycleStage.PROCESS_CALL,
            state,
            reasons=json.loads(order_row.get("rejection_reasons_json") or "[]"),
            evidence={"client_order_id": coid},
            outcome=state,
        )

        if state == OrderState.REJECTED.value:
            self._finish(
                row,
                stage=CycleStage.PROCESS_CALL,
                status="blocked",
                decision="reject",
                reasons=json.loads(order_row.get("rejection_reasons_json") or "[]"),
                notes=notes + ["call_hard_blocked"],
                stock_coid=stock_coid,
                call_coid=coid,
            )
            return

        if state == OrderState.AWAITING_APPROVAL.value:
            self._update(
                row,
                stage=CycleStage.PROCESS_CALL.value,
                status="awaiting_approval",
                notes=notes + ["call_routed_to_approval"],
                stock_client_order_id=stock_coid,
                call_client_order_id=coid,
                payload=payload,
            )
            return

        if state == OrderState.READY.value and (
            is_trading_paused(environ=self._environ) or self._config.mode.value != "paper_auto"
        ):
            self._update(
                row,
                stage=CycleStage.RECONCILE.value,
                status="in_progress",
                notes=notes + ["call_ready_dry_run_hold"],
                stock_client_order_id=stock_coid,
                call_client_order_id=coid,
                payload=payload,
            )
            return

        if state == OrderState.READY.value:
            submitted = self._orders.submit_ready_order(coid)
            coid = submitted["client_order_id"]

        self._update(
            row,
            stage=CycleStage.RECONCILE.value,
            status="in_progress",
            notes=notes,
            stock_client_order_id=stock_coid,
            call_client_order_id=coid,
            payload=payload,
        )

    def _stage_reconcile(self, row: dict[str, Any]) -> None:
        notes = list(json.loads(row.get("notes_json") or "[]"))
        from dividend_agent.storage.repository import LedgerRepository

        recon = Reconciler(self._broker, LedgerRepository(self._conn)).reconcile()
        evidence = {
            "account_id": recon.account_id,
            "positions": recon.positions,
            "orders": recon.orders,
            "mismatches": recon.mismatches,
        }
        self._record(
            row["cycle_id"],
            CycleStage.RECONCILE,
            "reconciled",
            reasons=list(recon.mismatches) or ["ok"],
            evidence=evidence,
            outcome="completed",
        )
        self._finish(
            row,
            stage=CycleStage.COMPLETE,
            status="completed",
            decision="complete",
            reasons=["cycle_complete"],
            notes=notes + ["reconciled"],
            stock_coid=row.get("stock_client_order_id"),
            call_coid=row.get("call_client_order_id"),
            evidence=evidence,
            memo_id=row.get("memo_id"),
            research_decision=row.get("research_decision"),
        )

    # --- helpers ---

    def _coverage_for_symbol(self, symbol: str, account_id: str) -> CoverageState | None:
        from dividend_agent.domain.models import TradeIntent

        probe = TradeIntent(
            account_id=account_id,
            symbol=symbol.upper(),
            side="buy",
            quantity=Decimal("1"),
            order_type="limit",
            limit_price=Decimal("1"),
            rule_config_version="v1",
            extras={"underlying": symbol.upper(), "intent_kind": "stock_buy"},
        )
        return derive_coverage(broker=self._broker, repo=self._exec, intent=probe)

    def _load_chain(self, symbol: str) -> OptionChain | None:
        if (
            self._option_chain is not None
            and self._option_chain.underlying.upper() == symbol.upper()
        ):
            return self._option_chain
        if self._option_chain_path and self._option_chain_path.exists():
            chain = load_chain_json(self._option_chain_path)
            if chain.underlying.upper() == symbol.upper():
                return chain
        if self._market_data is not None:
            try:
                return self._market_data.get_option_chain(symbol)
            except Exception:  # noqa: BLE001
                return None
        return None

    def _record(
        self,
        cycle_id: str,
        stage: CycleStage,
        decision: str,
        *,
        reasons: list[str],
        evidence: dict[str, Any] | None = None,
        outcome: str = "",
    ) -> None:
        self._cycles.record_decision(
            cycle_id=cycle_id,
            stage=stage.value,
            decision=decision,
            reasons=reasons,
            evidence=evidence,
            outcome=outcome,
        )
        if self._ledger is not None:
            self._ledger.append_audit(
                f"cycle.{stage.value}",
                {
                    "cycle_id": cycle_id,
                    "decision": decision,
                    "reasons": reasons,
                    "evidence": evidence or {},
                    "outcome": outcome,
                },
                correlation_id=cycle_id,
            )

    def _update(
        self,
        row: dict[str, Any],
        *,
        stage: str,
        status: str,
        notes: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        stock_client_order_id: str | None = None,
        call_client_order_id: str | None = None,
        memo_id: str | None = None,
        research_decision: str | None = None,
    ) -> None:
        updated = {
            **row,
            "stage": stage,
            "status": status,
            "notes_json": json.dumps(
                notes if notes is not None else json.loads(row.get("notes_json") or "[]")
            ),
            "evidence_json": json.dumps(
                evidence if evidence is not None else json.loads(row.get("evidence_json") or "{}")
            ),
            "payload_json": json.dumps(
                payload if payload is not None else json.loads(row.get("payload_json") or "{}")
            ),
        }
        if stock_client_order_id is not None:
            updated["stock_client_order_id"] = stock_client_order_id
        if call_client_order_id is not None:
            updated["call_client_order_id"] = call_client_order_id
        if memo_id is not None:
            updated["memo_id"] = memo_id
        if research_decision is not None:
            updated["research_decision"] = research_decision
        self._cycles.upsert_cycle(updated)

    def _finish(
        self,
        row: dict[str, Any],
        *,
        stage: CycleStage,
        status: str,
        decision: str,
        reasons: list[str],
        notes: list[str],
        stock_coid: str | None = None,
        call_coid: str | None = None,
        memo_id: str | None = None,
        evidence: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        research_decision: str | None = None,
    ) -> None:
        self._record(
            row["cycle_id"],
            stage,
            decision,
            reasons=reasons,
            evidence=evidence,
            outcome=status,
        )
        self._update(
            row,
            stage=stage.value,
            status=status,
            notes=notes,
            evidence=evidence,
            payload=payload,
            stock_client_order_id=stock_coid,
            call_client_order_id=call_coid,
            memo_id=memo_id,
            research_decision=research_decision or row.get("research_decision"),
        )

    def _outcome(self, row: dict[str, Any]) -> CycleOutcome:
        return CycleOutcome(
            cycle_id=row["cycle_id"],
            symbol=row["symbol"],
            stage=row["stage"],
            status=row["status"],
            notes=list(json.loads(row.get("notes_json") or "[]")),
            research_decision=row.get("research_decision"),
            stock_client_order_id=row.get("stock_client_order_id"),
            call_client_order_id=row.get("call_client_order_id"),
            payload=json.loads(row.get("payload_json") or "{}"),
        )


def run_supervised_cycle(
    *,
    config: AppConfig,
    broker: BrokerAdapter,
    conn: sqlite3.Connection,
    symbol: str,
    environ: dict[str, str] | None = None,
    market_data: MarketDataAdapter | None = None,
    research_pipeline: ResearchPipeline | None = None,
    option_chain_path: Path | None = None,
    option_chain: OptionChain | None = None,
    ex_div: ExDividendWindow | None = None,
    stock_limit_price: Decimal | None = None,
    cycle_id: str | None = None,
    resume: bool = False,
) -> CycleOutcome:
    orch = PaperCycleOrchestrator(
        config=config,
        broker=broker,
        conn=conn,
        environ=environ,
        market_data=market_data,
        research_pipeline=research_pipeline,
        option_chain_path=option_chain_path,
        option_chain=option_chain,
        ex_div=ex_div,
        stock_limit_price=stock_limit_price,
    )
    if resume and cycle_id:
        return orch.resume(cycle_id)
    return orch.start(symbol, cycle_id=cycle_id)
