"""Strategy configuration loading and validation.

Null execution guardrails fail closed for anything that would auto-submit
orders (spec §11, §21). Values for unresolved operator decisions stay null
until explicitly set — they are not invented here.
"""

from __future__ import annotations

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from dividend_agent.domain.models import BrokerEnvironment, OperatingMode

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


class ExDividendPolicy(StrEnum):
    """Ex-dividend overlap handling (spec §2.6 / §11)."""

    AVOID_OVERLAP = "avoid_overlap"
    # Approval-based spanning remains an open §21 decision; not a permissive default.


class StrikeFloor(StrEnum):
    WEIGHTED_AVERAGE_ACQUISITION_COST = "weighted_average_acquisition_cost"


class BrokerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["alpaca"] = "alpaca"
    environment: BrokerEnvironment = BrokerEnvironment.PAPER
    require_positive_paper_verification: bool = True


class CoveredCallsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_days_to_expiry: int = 30
    min_days_to_expiry: int = 21
    max_days_to_expiry: int = 45
    chance_of_profit_min: float = Field(default=0.90, ge=0.0, le=1.0)
    aggressive_mode_enabled: bool = False
    aggressive_probability_floor: float = Field(default=0.80, ge=0.0, le=1.0)
    strike_floor: StrikeFloor = StrikeFloor.WEIGHTED_AVERAGE_ACQUISITION_COST
    require_out_of_money_at_entry: bool = True
    contracts_per_standard_lot: int = 1
    skip_if_premium_not_worthwhile: bool = True
    ex_dividend_policy: ExDividendPolicy = ExDividendPolicy.AVOID_OVERLAP
    early_assignment_review_enabled: bool = True

    @model_validator(mode="after")
    def _expiry_bounds(self) -> CoveredCallsConfig:
        if not (self.min_days_to_expiry <= self.target_days_to_expiry <= self.max_days_to_expiry):
            raise ValueError("covered_calls days_to_expiry must satisfy min <= target <= max")
        return self


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dividend_yield_target_min: float = Field(default=0.05, ge=0.0)
    dividend_yield_target_max: float = Field(default=0.07, ge=0.0)
    unusually_high_yield_review_at: float = Field(default=0.09, ge=0.0)
    require_latest_10k: bool = True
    require_subsequent_10q_review: bool = True
    inspect_52_week_range: bool = True
    allow_partial_share_positions: bool = True
    covered_calls: CoveredCallsConfig = Field(default_factory=CoveredCallsConfig)

    @model_validator(mode="after")
    def _yield_range(self) -> StrategyConfig:
        if self.dividend_yield_target_min > self.dividend_yield_target_max:
            raise ValueError("dividend_yield_target_min must be <= dividend_yield_target_max")
        return self


class ExecutionConfig(BaseModel):
    """Execution settings. Null numeric guardrails are deployment blockers for auto-submit."""

    model_config = ConfigDict(extra="forbid")

    auto_paper_orders_enabled: bool = False
    human_approval_on_exceptions: bool = True
    hard_blocks_cannot_be_overridden: bool = True
    require_fresh_quote_seconds: int = Field(default=60, ge=1)
    require_idempotency_key: bool = True
    # Spec §11 / §21 — must be decided by operator before auto trading.
    max_position_pct: float | None = None
    max_portfolio_order_notional_usd: float | None = None
    min_cash_reserve_usd: float | None = None
    min_option_bid_usd_per_share: float | None = None
    max_option_bid_ask_spread_pct: float | None = None
    min_option_open_interest: int | None = None
    max_new_stock_order_notional_usd: float | None = None

    @property
    def unresolved_guardrails(self) -> list[str]:
        """Names of null guardrails that block autonomous submission."""
        fields = (
            "max_position_pct",
            "max_portfolio_order_notional_usd",
            "min_cash_reserve_usd",
            "min_option_bid_usd_per_share",
            "max_option_bid_ask_spread_pct",
            "min_option_open_interest",
            "max_new_stock_order_notional_usd",
        )
        return [name for name in fields if getattr(self, name) is None]


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sec_user_agent: str = ""
    max_age_days_for_research_memo: int = Field(default=30, ge=1)


class NotificationsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["file"] = "file"


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sqlite_path: str = "/data/agent.sqlite3"


class AppConfig(BaseModel):
    """Root strategy configuration (spec §11)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    mode: OperatingMode = OperatingMode.PAPER_DRY_RUN
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    @field_validator("mode")
    @classmethod
    def _no_live_mode(cls, value: OperatingMode) -> OperatingMode:
        # Defensive: only paper/research modes exist in the enum.
        return value

    @model_validator(mode="after")
    def _paper_only_broker(self) -> AppConfig:
        if self.broker.environment != BrokerEnvironment.PAPER:
            raise ValueError("v1 supports paper brokerage environment only")
        return self

    def auto_submit_blockers(self) -> list[str]:
        """Reasons auto-submit must fail closed. Empty only when fully configured for paper_auto."""
        blockers: list[str] = []
        if self.mode != OperatingMode.PAPER_AUTO:
            blockers.append(f"mode is {self.mode.value!r} (not paper_auto)")
        if not self.execution.auto_paper_orders_enabled:
            blockers.append("execution.auto_paper_orders_enabled is false")
        for name in self.execution.unresolved_guardrails:
            blockers.append(f"execution.{name} is null (unresolved §21 guardrail)")
        if not self.broker.require_positive_paper_verification:
            blockers.append("broker.require_positive_paper_verification must remain true")
        return blockers

    def assert_may_auto_submit(self) -> None:
        """Raise if autonomous order submission would be unsafe."""
        blockers = self.auto_submit_blockers()
        if blockers:
            raise AutoSubmitBlockedError(blockers)


class AutoSubmitBlockedError(RuntimeError):
    """Fail-closed refusal to auto-submit orders."""

    def __init__(self, blockers: list[str]) -> None:
        self.blockers = blockers
        joined = "; ".join(blockers)
        super().__init__(f"auto-submit blocked (fail closed): {joined}")


class ConfigError(ValueError):
    """Invalid or unreadable strategy configuration."""


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            return os.environ.get(key, "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: str | Path) -> AppConfig:
    """Load and validate a YAML strategy config file."""
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if raw is None:
        raise ConfigError(f"config file is empty: {config_path}")
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a mapping: {config_path}")
    expanded = _expand_env(raw)
    try:
        return AppConfig.model_validate(expanded)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def validate_config_file(path: str | Path) -> AppConfig:
    """Validate config and return the loaded model (raises ConfigError)."""
    return load_config(path)
