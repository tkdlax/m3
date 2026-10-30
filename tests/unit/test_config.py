"""Config validation and null-guardrail fail-closed tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dividend_agent.config import (
    AppConfig,
    AutoSubmitBlockedError,
    ConfigError,
    load_config,
)
from dividend_agent.domain.models import OperatingMode
from dividend_agent.execution.guardrails import (
    NullGuardrailError,
    assert_guardrails_resolved_for_auto_submit,
    null_execution_guardrails,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "config" / "strategy.example.yaml"


def test_example_config_loads() -> None:
    cfg = load_config(EXAMPLE)
    assert cfg.schema_version == 1
    assert cfg.mode == OperatingMode.PAPER_DRY_RUN
    assert cfg.broker.environment.value == "paper"
    assert cfg.execution.auto_paper_orders_enabled is False


def test_example_has_null_guardrails() -> None:
    cfg = load_config(EXAMPLE)
    unresolved = null_execution_guardrails(cfg)
    assert "max_position_pct" in unresolved
    assert "min_cash_reserve_usd" in unresolved
    assert len(unresolved) == 7


def test_auto_submit_blocked_on_example(tmp_path: Path) -> None:
    cfg = load_config(EXAMPLE)
    with pytest.raises(AutoSubmitBlockedError) as exc:
        cfg.assert_may_auto_submit()
    assert any("null" in b for b in exc.value.blockers)


def test_assert_guardrails_fail_closed_even_if_mode_paper_auto(tmp_path: Path) -> None:
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw["mode"] = "paper_auto"
    raw["execution"]["auto_paper_orders_enabled"] = True
    path = tmp_path / "strategy.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(path)
    with pytest.raises(NullGuardrailError) as exc:
        assert_guardrails_resolved_for_auto_submit(cfg)
    assert any("max_position_pct" in b for b in exc.value.blockers)


def test_resolved_guardrails_allow_assert(tmp_path: Path) -> None:
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw["mode"] = "paper_auto"
    raw["execution"].update(
        {
            "auto_paper_orders_enabled": True,
            "max_position_pct": 0.1,
            "max_portfolio_order_notional_usd": 5000.0,
            "min_cash_reserve_usd": 1000.0,
            "min_option_bid_usd_per_share": 0.05,
            "max_option_bid_ask_spread_pct": 0.25,
            "min_option_open_interest": 100,
            "max_new_stock_order_notional_usd": 2500.0,
        }
    )
    path = tmp_path / "strategy.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.auto_submit_blockers() == []
    assert_guardrails_resolved_for_auto_submit(cfg)


def test_invalid_yield_range_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw["strategy"]["dividend_yield_target_min"] = 0.10
    raw["strategy"]["dividend_yield_target_max"] = 0.05
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_file_raises() -> None:
    with pytest.raises(ConfigError):
        load_config("/nonexistent/strategy.yaml")


def test_default_mode_is_paper_dry_run() -> None:
    cfg = AppConfig()
    assert cfg.mode == OperatingMode.PAPER_DRY_RUN
