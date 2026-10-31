"""Parse OCC option symbols into contract fields."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from dividend_agent.options.contract_spec import OptionRight

_OCC = re.compile(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def parse_occ_symbol(symbol: str) -> dict[str, object] | None:
    """Return underlying/expiration/strike/right or None if not a standard OCC symbol."""
    text = symbol.strip().upper()
    match = _OCC.match(text)
    if not match:
        return None
    root, yy, mm, dd, cp, strike_raw = match.groups()
    year = 2000 + int(yy)
    expiration = date(year, int(mm), int(dd))
    strike = Decimal(strike_raw) / Decimal("1000")
    right = OptionRight.CALL if cp == "C" else OptionRight.PUT
    return {
        "symbol": text,
        "underlying": root,
        "expiration": expiration,
        "strike": strike,
        "right": right,
    }
