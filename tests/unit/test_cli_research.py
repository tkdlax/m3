"""CLI research screen command (offline)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from dividend_agent.cli import app

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "config" / "strategy.example.yaml"


def test_screen_fails_closed_without_data() -> None:
    result = runner.invoke(app, ["research", "screen", "GIS", "--config", str(EXAMPLE)])
    assert result.exit_code == 1


def test_screen_accepts_with_inputs() -> None:
    result = runner.invoke(
        app,
        [
            "research",
            "screen",
            "GIS",
            "--config",
            str(EXAMPLE),
            "--price",
            "40",
            "--week52-high",
            "50",
            "--week52-low",
            "30",
            "--avg-dollar-volume",
            "50000000",
            "--optionable",
            "--trailing-yield",
            "0.06",
        ],
    )
    assert result.exit_code == 0
    assert "true" in result.stdout.lower() or '"accepted": true' in result.stdout
