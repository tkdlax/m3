"""Ex-dividend overlap policy for covered calls (spec §2.6 / §11)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from dividend_agent.config import ExDividendPolicy


class ExDividendWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    ex_date: date
    source: str = "calendar"
    declared: bool = True


class ExDivCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    status: str  # pass | reject | review | missing
    detail: str
    ex_date: date | None = None


def call_spans_ex_dividend(
    *,
    as_of: date,
    expiration: date,
    ex_date: date,
) -> bool:
    """True if ex-date is strictly after as_of and on/before expiration."""
    return as_of < ex_date <= expiration


def evaluate_ex_dividend_policy(
    *,
    policy: ExDividendPolicy,
    as_of: date,
    expiration: date,
    window: ExDividendWindow | None,
) -> ExDivCheckResult:
    """Apply configured policy. Missing calendar with avoid_overlap => fail closed."""
    if window is None:
        if policy == ExDividendPolicy.AVOID_OVERLAP:
            return ExDivCheckResult(
                allowed=False,
                status="missing",
                detail="ex_dividend_calendar_missing_fail_closed",
            )
        return ExDivCheckResult(
            allowed=False,
            status="review",
            detail="ex_dividend_unknown_requires_review",
        )

    spans = call_spans_ex_dividend(as_of=as_of, expiration=expiration, ex_date=window.ex_date)
    if not spans:
        return ExDivCheckResult(
            allowed=True,
            status="pass",
            detail="no_ex_dividend_overlap",
            ex_date=window.ex_date,
        )

    if policy == ExDividendPolicy.AVOID_OVERLAP:
        return ExDivCheckResult(
            allowed=False,
            status="reject",
            detail="ex_dividend_overlap_avoid_policy",
            ex_date=window.ex_date,
        )

    # Future policies (approval-based) are not enabled as permissive defaults.
    return ExDivCheckResult(
        allowed=False,
        status="review",
        detail="ex_dividend_overlap_unresolved_policy",
        ex_date=window.ex_date,
    )
