"""Trading kill switch (TRADING_PAUSED / CLI)."""

from __future__ import annotations

import os


class TradingPausedError(RuntimeError):
    """Raised when new order submission is paused."""


def is_trading_paused(*, environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else dict(os.environ)
    raw = (env.get("TRADING_PAUSED") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def assert_trading_not_paused(*, environ: dict[str, str] | None = None) -> None:
    if is_trading_paused(environ=environ):
        raise TradingPausedError(
            "TRADING_PAUSED is set — new order submission is blocked "
            "(reconciliation/reporting still allowed)"
        )
