"""US equity exchange calendar helpers (spec §15).

Internal timestamps are UTC; display TZ may be America/Denver.
Holiday list covers NYSE-observed federal holidays for scheduling skips.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DISPLAY_TZ = ZoneInfo("America/Denver")
MARKET_TZ = ZoneInfo("America/New_York")


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """weekday: Mon=0 … Sun=6. n is 1-based."""
    d = date(year, month, 1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _observed(d: date) -> date:
    if d.weekday() == 5:  # Saturday → Friday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday → Monday
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> frozenset[date]:
    """Common NYSE full-day holidays (not early closes)."""
    holidays = {
        _observed(date(year, 1, 1)),  # New Year's
        _nth_weekday(year, 1, 0, 3),  # MLK
        _nth_weekday(year, 2, 0, 3),  # Presidents
        _last_weekday(year, 5, 0),  # Memorial
        _observed(date(year, 6, 19)),  # Juneteenth
        _observed(date(year, 7, 4)),  # Independence
        _nth_weekday(year, 9, 0, 1),  # Labor
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),  # Christmas
    }
    # Good Friday (Friday before Easter) — approximate via anonymous Gregorian
    holidays.add(_good_friday(year))
    return frozenset(holidays)


def _good_friday(year: int) -> date:
    # Anonymous Gregorian algorithm for Easter Sunday, then -2 days.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = ((h + el - 7 * m + 114) % 31) + 1
    easter = date(year, month, day)
    return easter - timedelta(days=2)


def is_trading_day(day: date | None = None) -> bool:
    day = day or datetime.now(tz=MARKET_TZ).date()
    if day.weekday() >= 5:
        return False
    return day not in nyse_holidays(day.year)


def trading_day_period_key(day: date | None = None) -> str:
    day = day or datetime.now(tz=MARKET_TZ).date()
    return day.isoformat()


def monthly_period_key(day: date | None = None) -> str:
    day = day or datetime.now(tz=MARKET_TZ).date()
    return f"{day.year:04d}-{day.month:02d}"


def now_utc() -> datetime:
    return datetime.now(tz=ZoneInfo("UTC"))


def display_now() -> datetime:
    return now_utc().astimezone(DISPLAY_TZ)
