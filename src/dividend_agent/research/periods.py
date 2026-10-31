"""XBRL / financial period classification and compatibility rules."""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class PeriodKind(StrEnum):
    INSTANT = "instant"
    QUARTERLY = "quarterly"
    YEAR_TO_DATE = "year_to_date"
    ANNUAL = "annual"
    TRAILING = "trailing"
    UNKNOWN = "unknown"


class PeriodRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: PeriodKind
    end: date | None = None
    start: date | None = None
    frame: str | None = None
    days: int | None = None


_FRAME_QY = re.compile(r"^CY(\d{4})Q([1-4])(I)?$")
_FRAME_Y = re.compile(r"^CY(\d{4})(I)?$")
_FRAME_YTD = re.compile(r"^CY(\d{4})Q([1-4])YTD$")


def classify_period(
    *,
    end: date | None,
    start: date | None = None,
    frame: str | None = None,
    form: str | None = None,
) -> PeriodRef:
    """Classify a fact period. Prefer explicit frame; else duration heuristics."""
    if frame:
        m = _FRAME_YTD.match(frame)
        if m:
            return PeriodRef(kind=PeriodKind.YEAR_TO_DATE, end=end, frame=frame)
        m = _FRAME_QY.match(frame)
        if m:
            kind = PeriodKind.INSTANT if m.group(3) == "I" else PeriodKind.QUARTERLY
            return PeriodRef(kind=kind, end=end, frame=frame)
        m = _FRAME_Y.match(frame)
        if m:
            kind = PeriodKind.INSTANT if m.group(2) == "I" else PeriodKind.ANNUAL
            return PeriodRef(kind=kind, end=end, frame=frame)

    days: int | None = None
    if start and end:
        days = (end - start).days
        if 80 <= days <= 100:
            return PeriodRef(kind=PeriodKind.QUARTERLY, end=end, start=start, days=days)
        if 360 <= days <= 370:
            return PeriodRef(kind=PeriodKind.ANNUAL, end=end, start=start, days=days)
        if 170 <= days <= 280:
            return PeriodRef(kind=PeriodKind.YEAR_TO_DATE, end=end, start=start, days=days)

    if form and form.upper() in {"10-K", "10-K/A"} and end and start is None:
        # Instant balance-sheet style or annual without start — unknown duration.
        return PeriodRef(kind=PeriodKind.UNKNOWN, end=end, frame=frame, days=days)

    if start is None and end is not None:
        return PeriodRef(kind=PeriodKind.INSTANT, end=end, frame=frame)

    return PeriodRef(kind=PeriodKind.UNKNOWN, end=end, start=start, frame=frame, days=days)


def periods_compatible_for_sum(a: PeriodRef, b: PeriodRef) -> bool:
    """True when two component facts can be added (same kind + same end)."""
    if a.end is None or b.end is None:
        return False
    if a.end != b.end:
        return False
    if a.kind != b.kind:
        return False
    return a.kind != PeriodKind.UNKNOWN


def prefer_period(a: PeriodRef, b: PeriodRef) -> PeriodRef:
    """Prefer more specific / non-amended-friendly annual/quarterly over unknown."""
    rank = {
        PeriodKind.ANNUAL: 4,
        PeriodKind.QUARTERLY: 3,
        PeriodKind.YEAR_TO_DATE: 2,
        PeriodKind.INSTANT: 2,
        PeriodKind.TRAILING: 1,
        PeriodKind.UNKNOWN: 0,
    }
    return a if rank[a.kind] >= rank[b.kind] else b
