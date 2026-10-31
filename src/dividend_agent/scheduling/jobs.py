"""Concrete scheduler jobs (startup / trading-day / monthly / events)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from dividend_agent.brokers.interface import BrokerAdapter, PaperNotVerifiedError
from dividend_agent.config import AppConfig
from dividend_agent.domain.models import OperatingMode
from dividend_agent.execution.kill_switch import is_trading_paused
from dividend_agent.execution.reconciliation import Reconciler
from dividend_agent.market_data.interface import MarketDataAdapter, MarketDataError
from dividend_agent.reporting.alerts import AlertSink
from dividend_agent.reporting.monthly import build_monthly_report, write_monthly_report
from dividend_agent.scheduling.calendar import (
    is_trading_day,
    monthly_period_key,
    now_utc,
    trading_day_period_key,
)
from dividend_agent.scheduling.runner import JobResult, SchedulerRunner
from dividend_agent.storage.execution_repository import ExecutionRepository


@dataclass
class JobDeps:
    config: AppConfig
    broker: BrokerAdapter
    reports_dir: Path
    environ: dict[str, str] | None = None
    # Optional: symbols for monthly research refresh (offline-safe empty default)
    research_symbols: tuple[str, ...] = ()
    # Replay/testing only — production uses market_data live chains.
    option_chain_path: Path | None = None
    market_data: MarketDataAdapter | None = None


def register_default_jobs(runner: SchedulerRunner, deps: JobDeps) -> None:
    runner.register("startup", lambda r, pk: job_startup(r, pk, deps))
    runner.register("trading_day", lambda r, pk: job_trading_day(r, pk, deps))
    runner.register("monthly", lambda r, pk: job_monthly(r, pk, deps))
    runner.register("event_review", lambda r, pk: job_event_review(r, pk, deps))


def job_startup(runner: SchedulerRunner, period_key: str, deps: JobDeps) -> JobResult:
    """Verify paper, schema, secrets, clock, capabilities; reconcile before decisions."""
    alerts = AlertSink(runner.conn, reports_dir=deps.reports_dir)
    checks: dict[str, Any] = {
        "schema_ok": True,
        "clock_utc": now_utc().isoformat(),
        "mode": deps.config.mode.value,
        "trading_paused": is_trading_paused(environ=deps.environ),
    }

    # Secrets: paper keys presence (values never logged)
    env = deps.environ if deps.environ is not None else dict(os.environ)
    has_key = bool(env.get("ALPACA_PAPER_API_KEY"))
    has_secret = bool(env.get("ALPACA_PAPER_API_SECRET"))
    checks["secrets_present"] = has_key and has_secret

    verification = deps.broker.verify_paper()
    checks["paper_status"] = verification.status.value
    checks["capabilities"] = list(verification.capabilities)
    if not verification.is_verified_paper:
        alerts.emit(
            "paper_unverified",
            "Startup paper verification failed",
            body=verification.detail,
            severity="error",
            payload=checks,
        )
        raise PaperNotVerifiedError(verification)

    reconciler = Reconciler(deps.broker, runner.ledger)
    recon = reconciler.reconcile()
    checks["reconcile"] = {
        "account_id": recon.account_id,
        "positions": recon.positions,
        "orders": recon.orders,
        "activities": recon.activities,
        "mismatches": recon.mismatches,
    }
    if recon.mismatches:
        alerts.emit(
            "reconcile_mismatch",
            "Startup reconcile mismatches",
            body="; ".join(recon.mismatches),
            severity="warning",
            payload=checks,
        )
    # Expire stale approvals as part of startup hygiene
    from dividend_agent.execution.approval_queue import ApprovalQueue

    expired = ApprovalQueue(ExecutionRepository(runner.conn)).expire_stale()
    checks["approvals_expired"] = expired

    # Market-data capability discovery (read-only). Missing caps are recorded;
    # monthly live evaluation fails closed separately when caps are absent.
    if deps.market_data is not None:
        try:
            md_caps = list(deps.market_data.discover_capabilities())
            checks["market_data_capabilities"] = md_caps
            checks["market_data_ready"] = bool(md_caps)
        except MarketDataError as exc:
            checks["market_data_capabilities"] = []
            checks["market_data_ready"] = False
            checks["market_data_error"] = str(exc)
            alerts.emit(
                "market_data_unavailable",
                "Startup market-data discovery failed",
                body=str(exc),
                severity="warning",
                payload=checks,
            )
    else:
        checks["market_data_capabilities"] = []
        checks["market_data_ready"] = False

    return JobResult(
        job_name="startup",
        period_key=period_key,
        status="completed",
        detail=f"startup ok account={recon.account_id}",
        payload=checks,
    )


def job_trading_day(runner: SchedulerRunner, period_key: str, deps: JobDeps) -> JobResult:
    """Open orders, assignment/expiry hints, drift, dividends, alerts."""
    day = date.fromisoformat(period_key) if period_key else None
    if not is_trading_day(day):
        return JobResult(
            job_name="trading_day",
            period_key=period_key,
            status="skipped_holiday",
            detail=f"non-trading day {period_key}",
            payload={"trading_day": False},
        )

    alerts = AlertSink(runner.conn, reports_dir=deps.reports_dir)
    paused = is_trading_paused(environ=deps.environ)

    # Always reconcile / inspect; trading actions respect pause
    verification = deps.broker.verify_paper()
    if not verification.is_verified_paper:
        raise PaperNotVerifiedError(verification)

    recon = Reconciler(deps.broker, runner.ledger).reconcile()
    account_id = recon.account_id
    open_orders = [
        o
        for o in deps.broker.list_orders(status="open", limit=100)
        if (o.status or "").lower()
        not in {"filled", "canceled", "cancelled", "expired", "rejected"}
    ]
    activities = deps.broker.list_activities(page_size=50)
    assign_like = [
        a
        for a in activities
        if (a.activity_type or "").upper() in {"OPASN", "ASSIGN", "EXERCISE", "OPEXP", "EXP"}
    ]
    dividends = [a for a in activities if (a.activity_type or "").upper().startswith("DIV")]

    payload: dict[str, Any] = {
        "account_id": account_id,
        "open_orders": len(open_orders),
        "assignments_or_expiry": len(assign_like),
        "dividend_events": len(dividends),
        "mismatches": recon.mismatches,
        "trading_paused": paused,
        "mode": deps.config.mode.value,
    }

    if recon.mismatches:
        alerts.emit(
            "account_drift",
            "Trading-day reconcile mismatch",
            body="; ".join(recon.mismatches),
            severity="warning",
            payload=payload,
        )
    if assign_like:
        alerts.emit(
            "assignment_or_expiry",
            "Assignment/expiry activity detected",
            body=f"{len(assign_like)} event(s)",
            severity="warning",
            payload={"count": len(assign_like)},
        )
    if dividends:
        alerts.emit(
            "dividend_event",
            "Dividend activity",
            body=f"{len(dividends)} event(s)",
            severity="info",
            payload={"count": len(dividends)},
        )

    # Recover ambiguous local orders (read/repair; submit only if not paused + paper_auto)
    exec_repo = ExecutionRepository(runner.conn)
    ambiguous = [
        *exec_repo.list_orders(state="submitted"),
        *exec_repo.list_orders(state="reconciliation_needed"),
    ]
    recovered = 0
    if ambiguous and not paused and deps.config.mode == OperatingMode.PAPER_AUTO:
        from dividend_agent.execution.approval_queue import ApprovalQueue
        from dividend_agent.execution.order_manager import OrderManager

        mgr = OrderManager(
            config=deps.config,
            broker=deps.broker,
            repo=exec_repo,
            approvals=ApprovalQueue(exec_repo),
            environ=deps.environ,
        )
        for row in ambiguous:
            try:
                mgr.recover_after_restart(row["client_order_id"])
                recovered += 1
            except Exception as exc:  # noqa: BLE001
                alerts.emit(
                    "order_recovery_failed",
                    "Order recovery failed",
                    body=str(exc),
                    severity="error",
                    payload={"client_order_id": row["client_order_id"]},
                )
    elif ambiguous and paused:
        alerts.emit(
            "trading_paused",
            "Trading paused — skipped order recovery submits",
            body=f"{len(ambiguous)} ambiguous order(s) left untouched",
            severity="info",
            payload={"ambiguous": len(ambiguous)},
        )

    # Advance open supervised cycles (fills / approvals / restart-safe stages).
    cycle_advances = _advance_open_cycles(runner, deps, paused=paused)
    payload["recovered_orders"] = recovered
    payload["cycle_advances"] = cycle_advances
    detail = (
        f"trading_day open_orders={len(open_orders)} paused={paused} "
        f"recovered={recovered} cycles={len(cycle_advances)}"
    )
    return JobResult(
        job_name="trading_day",
        period_key=period_key,
        status="skipped_paused" if paused else "completed",
        detail=detail,
        payload=payload,
    )


def job_monthly(runner: SchedulerRunner, period_key: str, deps: JobDeps) -> JobResult:
    """Research refresh, evaluate 100-share lots, select calls, reinvestment review."""
    alerts = AlertSink(runner.conn, reports_dir=deps.reports_dir)
    paused = is_trading_paused(environ=deps.environ)

    verification = deps.broker.verify_paper()
    if not verification.is_verified_paper:
        raise PaperNotVerifiedError(verification)
    recon = Reconciler(deps.broker, runner.ledger).reconcile()
    holdings = runner.ledger.list_holdings(recon.account_id)

    lots_100: list[dict[str, Any]] = []
    partials: list[dict[str, Any]] = []
    for h in holdings:
        if h.asset_class and "option" in h.asset_class.lower():
            continue
        qty = h.qty
        full = int(qty // Decimal("100"))
        rem = qty % Decimal("100")
        if full > 0:
            lots_100.append(
                {
                    "symbol": h.symbol,
                    "full_lots": full,
                    "qty": str(qty),
                    "avg_entry": str(h.avg_entry_price),
                }
            )
        if rem > 0:
            partials.append(
                {
                    "symbol": h.symbol,
                    "remainder_shares": str(rem),
                    "note": "partial_share_accumulation_candidate",
                }
            )

    # Milestone D: invoke research + supervised cycle (not just a refresh note).
    orchestration = _run_monthly_orchestration(
        runner,
        deps,
        account_id=recon.account_id,
        lots_100=lots_100,
        paused=paused,
        alerts=alerts,
    )
    research_note = orchestration.get("research_note", "no research symbols configured")
    call_selection = orchestration.get(
        "call_selection",
        {
            "evaluated_lots": len(lots_100),
            "intents": 0,
            "skipped": [],
            "trading_paused": paused,
            "mode": deps.config.mode.value,
        },
    )

    report = build_monthly_report(runner.conn, account_id=recon.account_id, period_key=period_key)
    report.skipped_opportunities = list(call_selection.get("skipped") or [])
    report_path = write_monthly_report(report, deps.reports_dir)
    alerts.emit(
        "monthly_report",
        f"Monthly report {period_key}",
        body=str(report_path),
        severity="info",
        payload=report.to_dict(),
    )

    payload = {
        "account_id": recon.account_id,
        "lots_100": lots_100,
        "partial_accumulation": partials,
        "research": research_note,
        "call_selection": call_selection,
        "cycles": orchestration.get("cycles", []),
        "report_path": str(report_path),
        "trading_paused": paused,
    }
    # Research + report always run; pause only suppresses submit-side work.
    return JobResult(
        job_name="monthly",
        period_key=period_key,
        status="completed",
        detail=f"monthly lots={len(lots_100)} report={report_path.name} paused={paused}",
        payload=payload,
    )


def job_event_review(runner: SchedulerRunner, period_key: str, deps: JobDeps) -> JobResult:
    """Focused review for fills, corporate actions, outages, mismatches."""
    # period_key encodes event type: e.g. fill:2026-10-30T12:00:00+00:00
    event_type, _, rest = period_key.partition(":")
    alerts = AlertSink(runner.conn, reports_dir=deps.reports_dir)
    alerts.emit(
        "event_review",
        f"Event review: {event_type or 'unknown'}",
        body=rest or period_key,
        severity="warning",
        payload={"event_type": event_type or "unknown", "ref": rest},
    )
    # Reconcile if broker is available
    try:
        verification = deps.broker.verify_paper()
        if verification.is_verified_paper:
            recon = Reconciler(deps.broker, runner.ledger).reconcile()
            payload = {
                "event_type": event_type or "unknown",
                "account_id": recon.account_id,
                "mismatches": recon.mismatches,
            }
            if recon.mismatches:
                alerts.emit(
                    "broker_mismatch",
                    "Mismatch during event review",
                    body="; ".join(recon.mismatches),
                    severity="error",
                    payload=payload,
                )
            return JobResult(
                job_name="event_review",
                period_key=period_key,
                status="completed",
                detail=f"reviewed {period_key}",
                payload=payload,
            )
    except Exception as exc:  # noqa: BLE001
        alerts.emit(
            "data_outage",
            "Event review broker/data failure",
            body=str(exc),
            severity="error",
            payload={"event": period_key},
        )
        return JobResult(
            job_name="event_review",
            period_key=period_key,
            status="completed",
            detail=f"recorded outage for {period_key}: {exc}",
            payload={"error": str(exc)},
        )
    return JobResult(
        job_name="event_review",
        period_key=period_key,
        status="completed",
        detail=f"reviewed {period_key} (paper unverified)",
        payload={"event_type": event_type or "unknown"},
    )


def _advance_open_cycles(
    runner: SchedulerRunner, deps: JobDeps, *, paused: bool
) -> list[dict[str, Any]]:
    """Resume in-progress paper cycles (fill confirmation, call selection)."""
    from dividend_agent.orchestration.paper_cycle import PaperCycleOrchestrator
    from dividend_agent.storage.cycle_repository import CycleRepository

    outcomes: list[dict[str, Any]] = []
    repo = CycleRepository(runner.conn)
    open_cycles = repo.list_open_cycles()
    if not open_cycles:
        return outcomes
    orch = PaperCycleOrchestrator(
        config=deps.config,
        broker=deps.broker,
        conn=runner.conn,
        environ=deps.environ,
        market_data=deps.market_data,
        option_chain_path=deps.option_chain_path,
    )
    for row in open_cycles:
        try:
            # Paused: still allow await_fill / research progress, but gateway submit stays blocked.
            _ = paused
            out = orch.resume(row["cycle_id"])
            outcomes.append(out.to_dict())
        except Exception as exc:  # noqa: BLE001
            outcomes.append(
                {
                    "cycle_id": row["cycle_id"],
                    "symbol": row["symbol"],
                    "status": "error",
                    "notes": [str(exc)],
                }
            )
    return outcomes


def _run_monthly_orchestration(
    runner: SchedulerRunner,
    deps: JobDeps,
    *,
    account_id: str,
    lots_100: list[dict[str, Any]],
    paused: bool,
    alerts: AlertSink,
) -> dict[str, Any]:
    """Research configured symbols and run/advance supervised cycles + call selection."""
    from dividend_agent.orchestration.paper_cycle import PaperCycleOrchestrator
    from dividend_agent.research.pipeline import ResearchPipeline

    symbols = tuple(s.upper() for s in deps.research_symbols)
    research_note = (
        f"research_invoked_for={list(symbols)}" if symbols else "no research symbols configured"
    )
    cycles: list[dict[str, Any]] = []
    pipeline: ResearchPipeline | None = None
    if symbols:
        try:
            pipeline = ResearchPipeline.from_config(deps.config)
        except Exception as exc:  # noqa: BLE001
            research_note = f"research_pipeline_unavailable:{exc}"
            alerts.emit(
                "research_unavailable",
                "Monthly research pipeline unavailable",
                body=str(exc),
                severity="warning",
            )

    if pipeline is not None and symbols:
        orch = PaperCycleOrchestrator(
            config=deps.config,
            broker=deps.broker,
            conn=runner.conn,
            environ=deps.environ,
            market_data=deps.market_data,
            research_pipeline=pipeline,
            option_chain_path=deps.option_chain_path,
        )
        for sym in symbols:
            try:
                out = orch.start(sym, account_id=account_id)
                cycles.append(out.to_dict())
            except Exception as exc:  # noqa: BLE001
                cycles.append({"symbol": sym, "status": "error", "notes": [str(exc)]})
                alerts.emit(
                    "cycle_failed",
                    f"Supervised cycle failed for {sym}",
                    body=str(exc),
                    severity="error",
                )

    # Call selection against confirmed 100-share lots (never against unfilled buys).
    call_selection: dict[str, Any] = {
        "evaluated_lots": len(lots_100),
        "intents": 0,
        "skipped": [],
        "trading_paused": paused,
        "mode": deps.config.mode.value,
    }
    if lots_100 and deps.option_chain_path and deps.option_chain_path.exists():
        call_selection.update(_evaluate_chain_for_lots(deps, lots_100, paused=paused))
        call_selection["chain_mode"] = "fixture_replay"
        # Route executable intents through gateway (dry-run safe by default).
        call_selection.update(
            _gateway_process_fixture_intents(runner, deps, lots_100, paused=paused)
        )
    elif lots_100 and deps.market_data is not None:
        try:
            call_selection.update(_evaluate_live_market_data(deps, lots_100, paused=paused))
            call_selection["chain_mode"] = "live_market_data"
            call_selection.update(
                _gateway_process_live_intents(runner, deps, lots_100, paused=paused)
            )
        except (MarketDataError, PaperNotVerifiedError) as exc:
            call_selection["skipped"].append(f"market_data_unavailable:{exc}")
            call_selection["chain_mode"] = "failed_closed"
            alerts.emit(
                "market_data_unavailable",
                "Monthly call selection skipped — market data unavailable",
                body=str(exc),
                severity="error",
                payload={"error": str(exc)},
            )
    elif lots_100:
        call_selection["skipped"].append(
            "market_data_unconfigured — no live chain; fixture path not provided"
        )
        call_selection["chain_mode"] = "unconfigured"
    else:
        call_selection["skipped"].append("no_100_share_lots — research/report only")
        call_selection["chain_mode"] = "no_lots"

    return {
        "research_note": research_note,
        "cycles": cycles,
        "call_selection": call_selection,
    }


def _gateway_process_fixture_intents(
    runner: SchedulerRunner,
    deps: JobDeps,
    lots_100: list[dict[str, Any]],
    *,
    paused: bool,
) -> dict[str, Any]:
    """Process selected fixture intents through OrderManager (no inventing fills)."""
    from datetime import UTC, datetime, time
    from datetime import date as date_cls

    from dividend_agent.execution.approval_queue import ApprovalQueue
    from dividend_agent.execution.order_manager import OrderManager
    from dividend_agent.options.chains import load_chain_json
    from dividend_agent.portfolio.lots import CoverageState, ShareLot
    from dividend_agent.storage.execution_repository import ExecutionRepository
    from dividend_agent.strategy.candidate_selection import select_covered_call_intents

    if deps.option_chain_path is None or not deps.option_chain_path.exists():
        return {"gateway_processed": 0}
    chain = load_chain_json(deps.option_chain_path)
    lot_match = next((x for x in lots_100 if x["symbol"] == chain.underlying), None)
    if lot_match is None:
        return {"gateway_processed": 0, "gateway_skipped": ["underlying_not_in_lots"]}
    coverage = CoverageState(
        symbol=lot_match["symbol"],
        lots=(
            ShareLot(
                qty=Decimal(lot_match["qty"]),
                cost_per_share=Decimal(lot_match["avg_entry"]),
            ),
        ),
    )
    chain_as_of = chain.as_of or date_cls.today()
    result = select_covered_call_intents(
        config=deps.config,
        chain=chain,
        coverage=coverage,
        account_id="scheduler",
        as_of=chain_as_of,
        now=datetime.combine(chain_as_of, time(15, 0), tzinfo=UTC),
    )
    exec_repo = ExecutionRepository(runner.conn)
    mgr = OrderManager(
        config=deps.config,
        broker=deps.broker,
        repo=exec_repo,
        approvals=ApprovalQueue(exec_repo),
        environ=deps.environ,
    )
    processed = 0
    states: list[str] = []
    for intent in result.intents:
        # Never submit when paused — process_intent still validates / dry-runs.
        row = mgr.process_intent(intent, coverage=coverage)
        processed += 1
        states.append(row["state"])
        if not paused and deps.config.mode.value == "paper_auto" and row["state"] == "ready":
            try:
                row = mgr.submit_ready_order(row["client_order_id"])
                states[-1] = row["state"]
            except Exception as exc:  # noqa: BLE001
                states[-1] = f"submit_blocked:{exc}"
    return {
        "gateway_processed": processed,
        "gateway_states": states,
        "intents": len(result.intents),
        "note": "intents routed through gateway; submission gated by mode/pause",
    }


def _gateway_process_live_intents(
    runner: SchedulerRunner,
    deps: JobDeps,
    lots_100: list[dict[str, Any]],
    *,
    paused: bool,
) -> dict[str, Any]:
    from dividend_agent.execution.approval_queue import ApprovalQueue
    from dividend_agent.execution.order_manager import OrderManager
    from dividend_agent.market_data.service import evaluate_underlying_live
    from dividend_agent.portfolio.lots import CoverageState, ShareLot
    from dividend_agent.storage.execution_repository import ExecutionRepository

    assert deps.market_data is not None
    exec_repo = ExecutionRepository(runner.conn)
    mgr = OrderManager(
        config=deps.config,
        broker=deps.broker,
        repo=exec_repo,
        approvals=ApprovalQueue(exec_repo),
        environ=deps.environ,
    )
    processed = 0
    states: list[str] = []
    for lot in lots_100:
        coverage = CoverageState(
            symbol=str(lot["symbol"]),
            lots=(
                ShareLot(
                    qty=Decimal(str(lot["qty"])),
                    cost_per_share=Decimal(str(lot["avg_entry"])),
                ),
            ),
        )
        try:
            result, _meta = evaluate_underlying_live(
                config=deps.config,
                market_data=deps.market_data,
                coverage=coverage,
                account_id="scheduler",
            )
        except MarketDataError:
            continue
        for intent in result.intents:
            row = mgr.process_intent(intent, coverage=coverage)
            processed += 1
            states.append(row["state"])
            if not paused and deps.config.mode.value == "paper_auto" and row["state"] == "ready":
                try:
                    row = mgr.submit_ready_order(row["client_order_id"])
                    states[-1] = row["state"]
                except Exception as exc:  # noqa: BLE001
                    states[-1] = f"submit_blocked:{exc}"
    return {"gateway_processed": processed, "gateway_states": states}


def _evaluate_chain_for_lots(
    deps: JobDeps, lots_100: list[dict[str, Any]], *, paused: bool
) -> dict[str, Any]:
    """Fixture/replay evaluation — anchors time to chain as_of (not production)."""
    from datetime import UTC, datetime, time
    from datetime import date as date_cls

    from dividend_agent.options.chains import load_chain_json
    from dividend_agent.portfolio.lots import CoverageState, ShareLot
    from dividend_agent.strategy.candidate_selection import select_covered_call_intents

    assert deps.option_chain_path is not None
    chain = load_chain_json(deps.option_chain_path)
    underlying = chain.underlying
    lot_match = next((x for x in lots_100 if x["symbol"] == underlying), lots_100[0])
    coverage = CoverageState(
        symbol=lot_match["symbol"],
        lots=(
            ShareLot(
                qty=Decimal(lot_match["qty"]),
                cost_per_share=Decimal(lot_match["avg_entry"]),
            ),
        ),
    )
    chain_as_of = chain.as_of or date_cls.today()
    eval_now = datetime.combine(chain_as_of, time(15, 0), tzinfo=UTC)
    cfg = deps.config
    result = select_covered_call_intents(
        config=cfg,
        chain=chain,
        coverage=coverage,
        account_id="scheduler",
        as_of=chain_as_of,
        now=eval_now,
    )
    return {
        "evaluated_lots": 1,
        "intents": len(result.intents),
        "skipped": list(result.skipped),
        "trading_paused": paused,
        "mode": cfg.mode.value,
        "note": "fixture replay; intents proposed only; submission gated",
        "fixture_used": True,
    }


def _evaluate_live_market_data(
    deps: JobDeps, lots_100: list[dict[str, Any]], *, paused: bool
) -> dict[str, Any]:
    """Production path: live quotes/chains/calendar; wall-clock; no fixture JSON."""
    from decimal import Decimal as Dec

    from dividend_agent.market_data.service import evaluate_underlying_live
    from dividend_agent.portfolio.lots import CoverageState, ShareLot

    assert deps.market_data is not None
    cfg = deps.config
    total_intents = 0
    skipped: list[str] = []
    metas: list[dict[str, Any]] = []
    for lot in lots_100:
        coverage = CoverageState(
            symbol=str(lot["symbol"]),
            lots=(
                ShareLot(
                    qty=Dec(str(lot["qty"])),
                    cost_per_share=Dec(str(lot["avg_entry"])),
                ),
            ),
        )
        try:
            result, meta = evaluate_underlying_live(
                config=cfg,
                market_data=deps.market_data,
                coverage=coverage,
                account_id="scheduler",
            )
            total_intents += len(result.intents)
            skipped.extend(list(result.skipped))
            metas.append(meta)
        except MarketDataError as exc:
            skipped.append(f"{lot['symbol']}:market_data:{exc}")
    return {
        "evaluated_lots": len(lots_100),
        "intents": total_intents,
        "skipped": skipped,
        "trading_paused": paused,
        "mode": cfg.mode.value,
        "note": "live market data; intents proposed only; submission gated",
        "fixture_used": False,
        "underlyings": metas,
    }


def default_period_for(job_name: str) -> str:
    if job_name == "startup":
        return now_utc().date().isoformat() + ":startup"
    if job_name == "trading_day":
        return trading_day_period_key()
    if job_name == "monthly":
        return monthly_period_key()
    if job_name == "event_review":
        return f"manual:{now_utc().isoformat()}"
    return now_utc().isoformat()
