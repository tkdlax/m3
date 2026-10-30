"""Command-line interface (M0 + M1 broker/ledger)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from dividend_agent import __version__
from dividend_agent.app import health_payload
from dividend_agent.brokers.factory import create_broker_adapter
from dividend_agent.brokers.interface import (
    BrokerConfigError,
    BrokerTransportError,
    PaperNotVerifiedError,
    require_verified_paper,
)
from dividend_agent.config import ConfigError, load_config
from dividend_agent.execution.guardrails import (
    NullGuardrailError,
    assert_guardrails_resolved_for_auto_submit,
    null_execution_guardrails,
)
from dividend_agent.execution.reconciliation import Reconciler
from dividend_agent.research.pipeline import ResearchPipeline
from dividend_agent.research.screener import MarketSnapshotInput
from dividend_agent.research.sec_edgar import SecEdgarError
from dividend_agent.storage import LedgerRepository, ResearchRepository, open_migrated

app = typer.Typer(
    name="dividend-agent",
    help="Paper-only dividend + covered-call agent (no live trading in v1).",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Strategy configuration commands.")
broker_app = typer.Typer(help="Broker paper-verification and ledger commands.")
ledger_app = typer.Typer(help="Local SQLite ledger status.")
research_app = typer.Typer(help="SEC research and issuer screening (read-only).")
app.add_typer(config_app, name="config")
app.add_typer(broker_app, name="broker")
app.add_typer(ledger_app, name="ledger")
app.add_typer(research_app, name="research")

console = Console(stderr=False)
err_console = Console(stderr=True)


def _resolve_db_path(config: Path | None, db: Path | None) -> Path:
    if db is not None:
        return db
    if config is not None:
        cfg = load_config(config)
        return Path(cfg.storage.sqlite_path)
    return Path("/data/agent.sqlite3")


@app.command("version")
def version_cmd() -> None:
    """Print package version."""
    console.print(__version__)


@app.command("health")
def health_cmd(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Optional strategy YAML for mode context."),
    ] = None,
) -> None:
    """Report process health (safe; does not contact brokers)."""
    loaded = None
    if config is not None:
        try:
            loaded = load_config(config)
        except ConfigError as exc:
            err_console.print(f"[red]config error:[/red] {exc}")
            raise typer.Exit(code=2) from exc
    console.print_json(json.dumps(health_payload(loaded)))


@config_app.command("validate")
def config_validate(
    path: Annotated[
        Path,
        typer.Argument(help="Path to strategy YAML (e.g. config/strategy.example.yaml)."),
    ],
    check_auto_submit: Annotated[
        bool,
        typer.Option(
            "--check-auto-submit",
            help="Also fail if unresolved null guardrails would block paper_auto.",
        ),
    ] = False,
) -> None:
    """Validate strategy configuration schema and paper-only constraints."""
    try:
        cfg = load_config(path)
    except ConfigError as exc:
        err_console.print(f"[red]invalid:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    table = Table(title="Config validation")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("schema_version", str(cfg.schema_version))
    table.add_row("mode", cfg.mode.value)
    table.add_row("broker.environment", cfg.broker.environment.value)
    table.add_row(
        "unresolved_guardrails",
        ", ".join(null_execution_guardrails(cfg)) or "(none)",
    )
    console.print(table)

    if check_auto_submit:
        try:
            assert_guardrails_resolved_for_auto_submit(cfg)
        except NullGuardrailError as exc:
            err_console.print(f"[red]auto-submit blocked:[/red] {exc}")
            raise typer.Exit(code=1) from exc

    console.print("[green]ok[/green] configuration is valid for dry-run / research.")
    if cfg.auto_submit_blockers():
        err_console.print(
            "[yellow]note:[/yellow] auto-submit remains fail-closed until mode, "
            "flags, and §21 guardrails are explicitly set."
        )


@broker_app.command("verify-paper")
def broker_verify_paper() -> None:
    """Positively verify Alpaca paper account; fail closed if unverified."""
    try:
        adapter = create_broker_adapter()
    except BrokerConfigError as exc:
        err_console.print(f"[red]fail closed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    result = adapter.verify_paper()
    console.print(
        {
            "status": result.status.value,
            "account_id": result.account_id,
            "detail": result.detail,
            "capabilities": list(result.capabilities),
        }
    )
    try:
        require_verified_paper(result)
    except PaperNotVerifiedError as exc:
        err_console.print(
            "[red]fail closed:[/red] paper account not verified; "
            "no orders may be submitted. Provide ALPACA_PAPER_API_KEY / "
            "ALPACA_PAPER_API_SECRET against paper-api.alpaca.markets."
        )
        raise typer.Exit(code=1) from exc
    console.print("[green]ok[/green] paper account verified (read-only M1).")


@broker_app.command("reconcile")
def broker_reconcile(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Strategy YAML for sqlite_path."),
    ] = None,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Override SQLite path."),
    ] = None,
) -> None:
    """Pull paper account/positions/orders/activities into SQLite. No trades."""
    try:
        db_path = _resolve_db_path(config, db)
    except ConfigError as exc:
        err_console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    try:
        adapter = create_broker_adapter()
    except BrokerConfigError as exc:
        err_console.print(f"[red]fail closed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    conn = open_migrated(db_path)
    try:
        repo = LedgerRepository(conn)
        reconciler = Reconciler(adapter, repo)
        result = reconciler.reconcile()
    except PaperNotVerifiedError as exc:
        err_console.print(f"[red]fail closed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except BrokerTransportError as exc:
        err_console.print(f"[red]broker error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()
        close = getattr(adapter, "close", None)
        if callable(close):
            close()

    console.print(
        {
            "account_id": result.account_id,
            "positions": result.positions,
            "orders": result.orders,
            "activities": result.activities,
            "job_id": result.job_id,
            "detail": result.detail,
            "mismatches": result.mismatches,
            "db": str(db_path),
        }
    )
    if result.mismatches:
        err_console.print("[yellow]warnings:[/yellow] " + "; ".join(result.mismatches))
    console.print("[green]ok[/green] reconciliation complete (no orders submitted).")


@broker_app.command("status")
def broker_status(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Strategy YAML for sqlite_path."),
    ] = None,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Override SQLite path."),
    ] = None,
) -> None:
    """Show last verified ledger snapshot from SQLite (no broker call required)."""
    try:
        db_path = _resolve_db_path(config, db)
    except ConfigError as exc:
        err_console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if not db_path.exists():
        err_console.print(
            f"[red]no ledger:[/red] {db_path} does not exist. "
            "Run `broker reconcile` after configuring paper credentials."
        )
        raise typer.Exit(code=1)

    conn = open_migrated(db_path)
    try:
        repo = LedgerRepository(conn)
        accounts = repo.list_accounts()
        if not accounts:
            err_console.print("[yellow]ledger empty:[/yellow] no accounts synced yet.")
            raise typer.Exit(code=1)
        payloads = []
        for acct in accounts:
            account_id = acct["account_id"]
            holdings = repo.list_holdings(account_id)
            job = repo.latest_job("reconcile")
            payloads.append(
                {
                    "account_id": account_id,
                    "status": acct["status"],
                    "cash": acct["cash"],
                    "equity": acct["equity"],
                    "buying_power": acct["buying_power"],
                    "holdings": len(holdings),
                    "orders": repo.count_orders(account_id),
                    "activities": repo.count_activities(account_id),
                    "synced_at": acct["synced_at"],
                    "last_reconcile": None
                    if job is None
                    else {
                        "id": job.id,
                        "status": job.status,
                        "finished_at": job.finished_at,
                        "detail": job.detail,
                    },
                }
            )
    finally:
        conn.close()

    console.print_json(json.dumps(payloads))


@ledger_app.command("migrate")
def ledger_migrate(
    db: Annotated[
        Path,
        typer.Option("--db", help="SQLite path to migrate."),
    ] = Path("/data/agent.sqlite3"),
) -> None:
    """Apply SQLite migrations (WAL) without contacting the broker."""
    conn = open_migrated(db)
    try:
        from dividend_agent.storage.db import current_schema_version

        version = current_schema_version(conn)
    finally:
        conn.close()
    console.print({"db": str(db), "schema_version": version})
    console.print("[green]ok[/green] migrations applied.")


def _parse_market_opts(
    price: float | None,
    week52_high: float | None,
    week52_low: float | None,
    avg_dollar_volume: float | None,
    optionable: bool | None,
) -> MarketSnapshotInput:
    from datetime import date
    from decimal import Decimal

    return MarketSnapshotInput(
        price=None if price is None else Decimal(str(price)),
        price_as_of=date.today() if price is not None else None,
        week52_high=None if week52_high is None else Decimal(str(week52_high)),
        week52_low=None if week52_low is None else Decimal(str(week52_low)),
        avg_dollar_volume=(None if avg_dollar_volume is None else Decimal(str(avg_dollar_volume))),
        optionable=optionable,
    )


@research_app.command("symbol")
def research_symbol_cmd(
    ticker: Annotated[str, typer.Argument(help="Ticker symbol to research.")],
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Strategy YAML."),
    ] = Path("config/strategy.example.yaml"),
    db: Annotated[
        Path | None,
        typer.Option("--db", help="SQLite path to persist memo (optional)."),
    ] = None,
    cache_dir: Annotated[
        Path | None,
        typer.Option("--cache-dir", help="SEC JSON cache directory."),
    ] = Path(".cache/sec"),
    price: Annotated[float | None, typer.Option(help="Market price (required for yields).")] = None,
    week52_high: Annotated[float | None, typer.Option(help="52-week high.")] = None,
    week52_low: Annotated[float | None, typer.Option(help="52-week low.")] = None,
    avg_dollar_volume: Annotated[float | None, typer.Option(help="Average dollar volume.")] = None,
    optionable: Annotated[bool | None, typer.Option(help="Optionable flag.")] = None,
) -> None:
    """Build a cited issuer research memo from SEC EDGAR (no orders)."""
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        err_console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    market = _parse_market_opts(price, week52_high, week52_low, avg_dollar_volume, optionable)
    pipeline: ResearchPipeline | None = None
    try:
        pipeline = ResearchPipeline.from_config(cfg, cache_dir=cache_dir)
        memo = pipeline.research_symbol(ticker, market=market)
    except SecEdgarError as exc:
        err_console.print(f"[red]research fail closed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if pipeline is not None:
            pipeline.sec.close()

    payload = {
        "ticker": memo.identity.ticker,
        "cik": memo.identity.cik,
        "decision": memo.decision,
        "decision_reasons": list(memo.decision_reasons),
        "unknowns": list(memo.unknowns),
        "thesis": memo.thesis,
        "criteria": [c.model_dump(mode="json") for c in memo.criteria],
        "citations": [c.model_dump(mode="json") for c in memo.citations[:20]],
        "filings": [c.model_dump(mode="json") for c in memo.filings[:10]],
        "memo_id": str(memo.memo_id),
    }
    console.print_json(json.dumps(payload))

    if db is not None:
        conn = open_migrated(db)
        try:
            ResearchRepository(conn).save_memo(memo)
        finally:
            conn.close()
        console.print(f"[green]ok[/green] memo persisted to {db}")

    if memo.decision == "reject":
        raise typer.Exit(code=1)


@research_app.command("screen")
def research_screen_cmd(
    ticker: Annotated[str, typer.Argument(help="Ticker to screen.")],
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Strategy YAML."),
    ] = Path("config/strategy.example.yaml"),
    price: Annotated[float | None, typer.Option(help="Market price.")] = None,
    week52_high: Annotated[float | None, typer.Option()] = None,
    week52_low: Annotated[float | None, typer.Option()] = None,
    avg_dollar_volume: Annotated[float | None, typer.Option()] = None,
    optionable: Annotated[bool | None, typer.Option()] = None,
    trailing_yield: Annotated[
        float | None,
        typer.Option(help="Override trailing yield (e.g. 0.06) when SEC dividends unavailable."),
    ] = None,
) -> None:
    """Run deterministic screen gates; missing data fails closed / review."""
    from decimal import Decimal

    from dividend_agent.research.dividends import (
        DividendLabel,
        DividendSnapshot,
    )
    from dividend_agent.research.screener import screen_issuer

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        err_console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    market = _parse_market_opts(price, week52_high, week52_low, avg_dollar_volume, optionable)
    unknowns: list[str] = []
    trailing = None if trailing_yield is None else Decimal(str(trailing_yield))
    if trailing is None:
        unknowns.append("trailing_yield_not_provided")
    div = DividendSnapshot(
        ticker=ticker.upper(),
        trailing_yield=trailing,
        trailing_yield_label=DividendLabel.TRAILING,
        price_for_yield=market.price,
        unknowns=tuple(unknowns),
    )
    result = screen_issuer(ticker, strategy=cfg.strategy, dividends=div, market=market)
    console.print_json(json.dumps(result.model_dump(mode="json")))
    if not result.accepted:
        raise typer.Exit(code=1)


def main() -> None:
    """Console script entrypoint."""
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
