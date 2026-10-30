"""CLI options evaluate."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from dividend_agent.cli import app

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[2]
CHAIN = ROOT / "tests" / "fixtures" / "option_chain_gis.json"
CONFIG = ROOT / "config" / "strategy.example.yaml"


def test_evaluate_fail_closed_without_liquidity_override() -> None:
    result = runner.invoke(
        app,
        [
            "options",
            "evaluate",
            str(CHAIN),
            "--config",
            str(CONFIG),
            "--shares",
            "100",
            "--wac",
            "40",
            "--as-of",
            "2026-03-01",
            "--ex-date",
            "2026-05-01",
        ],
    )
    assert result.exit_code == 1
    assert "unresolved_guardrail" in result.stdout or "no accepted" in result.stderr.lower()


def test_evaluate_with_liquidity_override() -> None:
    result = runner.invoke(
        app,
        [
            "options",
            "evaluate",
            str(CHAIN),
            "--config",
            str(CONFIG),
            "--shares",
            "100",
            "--wac",
            "40",
            "--as-of",
            "2026-03-01",
            "--ex-date",
            "2026-05-01",
            "--set-liquidity-guardrails",
        ],
    )
    # May still reject on COP depending on IV/spot; ensure command runs and is explainable
    assert "evaluations" in result.stdout
    assert "submission" in result.stdout
