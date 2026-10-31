"""Application entry helpers for dry-run startup."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dividend_agent import __version__
from dividend_agent.brokers.interface import (
    PaperNotVerifiedError,
    UnconfiguredBrokerAdapter,
    require_verified_paper,
)
from dividend_agent.config import AppConfig, load_config
from dividend_agent.domain.models import OperatingMode


def health_payload(
    config: AppConfig | None = None,
    *,
    liveness_only: bool = False,
    db_path: str | Path | None = None,
    environ: dict[str, str] | None = None,
    check_broker: bool = False,
) -> dict[str, Any]:
    """Health report.

    - ``liveness_only=True``: process liveness for Docker HEALTHCHECK (always ``ok``
      when the process can answer).
    - Otherwise, if a DB path is available, run operational readiness and map
      ready→ok, degraded→degraded, not_ready→unhealthy so health does **not**
      report ``ok`` while scheduled work is failing.
    """
    mode = config.mode.value if config else OperatingMode.PAPER_DRY_RUN.value
    base: dict[str, Any] = {
        "version": __version__,
        "mode": mode,
        "paper_only": True,
        "live_trading": False,
    }
    if liveness_only:
        return {**base, "status": "ok", "kind": "liveness"}

    env = environ if environ is not None else dict(os.environ)
    resolved: Path | None
    if db_path is not None:
        resolved = Path(db_path)
    elif env.get("DB_PATH"):
        resolved = Path(env["DB_PATH"])
    elif config is not None and Path(config.storage.sqlite_path).exists():
        # Use strategy path only when the file already exists (avoid default
        # /data/... off-host turning local health into a false not_ready).
        resolved = Path(config.storage.sqlite_path)
    else:
        resolved = None

    # No DB configured → process liveness only (early boot / local without data).
    if resolved is None:
        return {**base, "status": "ok", "kind": "liveness", "note": "no_db_path"}

    from dividend_agent.observability.readiness import assess_readiness

    report = assess_readiness(
        config=config,
        db_path=resolved,
        environ=env,
        check_broker=check_broker,
    )
    mapped = {"ready": "ok", "degraded": "degraded", "not_ready": "unhealthy"}.get(
        report.status, "unhealthy"
    )
    return {
        **base,
        "status": mapped,
        "kind": "operational",
        "readiness_status": report.status,
        "checks": report.to_dict()["checks"],
        "generated_at": report.generated_at,
    }


def readiness_payload(
    *,
    config: AppConfig | None = None,
    db_path: str | Path | None = None,
    environ: dict[str, str] | None = None,
    check_broker: bool = False,
) -> dict[str, Any]:
    """Operational readiness — DB/jobs/cycles/approvals; optional broker."""
    from dividend_agent.observability.readiness import assess_readiness

    return assess_readiness(
        config=config,
        db_path=db_path,
        environ=environ,
        check_broker=check_broker,
    ).to_dict()


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
