"""Command-line interface stubs for M0."""

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
from dividend_agent.brokers.interface import (
    PaperNotVerifiedError,
    UnconfiguredBrokerAdapter,
    require_verified_paper,
)
from dividend_agent.config import ConfigError, load_config
from dividend_agent.execution.guardrails import (
    NullGuardrailError,
    assert_guardrails_resolved_for_auto_submit,
    null_execution_guardrails,
)

app = typer.Typer(
    name="dividend-agent",
    help="Paper-only dividend + covered-call agent (no live trading in v1).",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Strategy configuration commands.")
broker_app = typer.Typer(help="Broker paper-verification commands.")
app.add_typer(config_app, name="config")
app.add_typer(broker_app, name="broker")

console = Console(stderr=False)
err_console = Console(stderr=True)


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
    """Positively verify Alpaca paper account (stub in M0 — fails closed)."""
    adapter = UnconfiguredBrokerAdapter()
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
            "[red]fail closed:[/red] paper account not verified; no orders may be submitted."
        )
        raise typer.Exit(code=1) from exc


def main() -> None:
    """Console script entrypoint."""
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
