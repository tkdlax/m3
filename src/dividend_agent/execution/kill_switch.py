"""Trading kill switch (TRADING_PAUSED / CLI)."""

from __future__ import annotations

import os

_PAUSED_VALUES = frozenset({"1", "true", "yes", "on"})
_UNPAUSED_VALUES = frozenset({"0", "false", "no", "off"})


class TradingPausedError(RuntimeError):
    """Raised when new order submission is paused."""


class InvalidPauseSettingError(TradingPausedError):
    """Raised when TRADING_PAUSED is set to an unrecognized value (fail closed)."""


def parse_trading_paused(raw: str | None) -> bool:
    """Return True if trading is paused.

    Unset / empty defaults to paused. Only exact false-ish tokens unpause.
    Any other non-empty value (e.g. ``typo``) is treated as paused.
    """
    if raw is None or not str(raw).strip():
        return True
    token = str(raw).strip().lower()
    # Only exact false-ish tokens unpause; true-ish and typos → paused.
    return token not in _UNPAUSED_VALUES


def is_trading_paused(*, environ: dict[str, str] | None = None) -> bool:
    """Read pause state.

    Live ``os.environ['TRADING_PAUSED']`` wins when set so a running gateway
    observes an operator stop without reconstructing managers. When live is
    unset, the optional ``environ`` overlay is used (tests / injected cycle env).
    """
    if "TRADING_PAUSED" in os.environ:
        return parse_trading_paused(os.environ.get("TRADING_PAUSED"))
    if environ is not None:
        return parse_trading_paused(environ.get("TRADING_PAUSED"))
    return True


def assert_trading_not_paused(*, environ: dict[str, str] | None = None) -> None:
    raw = (
        os.environ.get("TRADING_PAUSED")
        if "TRADING_PAUSED" in os.environ
        else (None if environ is None else environ.get("TRADING_PAUSED"))
    )
    if raw is not None and str(raw).strip():
        token = str(raw).strip().lower()
        if token not in _PAUSED_VALUES and token not in _UNPAUSED_VALUES:
            raise InvalidPauseSettingError(
                f"TRADING_PAUSED={raw!r} is invalid — treating as paused "
                "(allowed: true/false/1/0/yes/no/on/off)"
            )
    if is_trading_paused(environ=environ):
        raise TradingPausedError(
            "TRADING_PAUSED is set — new order submission is blocked "
            "(reconciliation/reporting still allowed)"
        )
