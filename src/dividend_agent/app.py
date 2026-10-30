"""Application entry helpers for dry-run startup."""

from __future__ import annotations

from pathlib import Path

from dividend_agent import __version__
from dividend_agent.brokers.interface import (
    PaperNotVerifiedError,
    UnconfiguredBrokerAdapter,
    require_verified_paper,
)
from dividend_agent.config import AppConfig, load_config
from dividend_agent.domain.models import OperatingMode


def health_payload(config: AppConfig | None = None) -> dict[str, object]:
    mode = config.mode.value if config else OperatingMode.PAPER_DRY_RUN.value
    return {
        "status": "ok",
        "version": __version__,
        "mode": mode,
        "paper_only": True,
        "live_trading": False,
    }


def boot_dry_run(config_path: str | Path) -> AppConfig:
    """Load config for a safe dry-run start. Does not submit orders."""
    config = load_config(config_path)
    # Dry-run / research may start with unresolved §21 limits.
    # Auto-submit remains blocked elsewhere.
    return config


def verify_paper_or_fail() -> None:
    """CLI helper: fail closed when paper is not positively verified."""
    adapter = UnconfiguredBrokerAdapter()
    result = adapter.verify_paper()
    require_verified_paper(result)
    raise PaperNotVerifiedError(result)  # pragma: no cover — unreachable while unverified
