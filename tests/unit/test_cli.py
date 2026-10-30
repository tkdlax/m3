"""CLI smoke tests via Typer runner."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from dividend_agent import __version__
from dividend_agent.cli import app

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "config" / "strategy.example.yaml"


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_health() -> None:
    result = runner.invoke(app, ["health", "--config", str(EXAMPLE)])
    assert result.exit_code == 0
    assert "paper_dry_run" in result.stdout
    assert "live_trading" in result.stdout


def test_config_validate_ok() -> None:
    result = runner.invoke(app, ["config", "validate", str(EXAMPLE)])
    assert result.exit_code == 0
    assert "ok" in result.stdout.lower() or "valid" in result.stdout.lower()


def test_config_validate_auto_submit_fails() -> None:
    result = runner.invoke(app, ["config", "validate", str(EXAMPLE), "--check-auto-submit"])
    assert result.exit_code == 1


def test_broker_verify_paper_fails_closed() -> None:
    result = runner.invoke(app, ["broker", "verify-paper"])
    assert result.exit_code == 1
