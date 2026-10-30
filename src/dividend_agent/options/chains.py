"""Option chain parsing from broker payloads or fixtures."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from dividend_agent.options.contract_spec import (
    OptionContract,
    OptionQuote,
    OptionRight,
    parse_contract,
    parse_quote,
)


class ChainEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract: OptionContract
    quote: OptionQuote


class OptionChain(BaseModel):
    model_config = ConfigDict(frozen=True)

    underlying: str
    underlying_price: Decimal | None = None
    as_of: date | None = None
    entries: tuple[ChainEntry, ...] = ()
    source: str = "fixture"

    def calls(self) -> list[ChainEntry]:
        return [e for e in self.entries if e.contract.right == OptionRight.CALL]


def parse_chain_payload(payload: dict[str, Any]) -> OptionChain:
    underlying = str(payload.get("underlying") or payload.get("symbol") or "").upper()
    price = payload.get("underlying_price") or payload.get("spot")
    underlying_price = Decimal(str(price)) if price is not None else None
    as_of_raw = payload.get("as_of")
    as_of = date.fromisoformat(str(as_of_raw)[:10]) if as_of_raw else None
    raw_entries = payload.get("options") or payload.get("contracts") or payload.get("entries") or []
    entries: list[ChainEntry] = []
    if not isinstance(raw_entries, list):
        raise ValueError("chain options must be a list")
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        contract_raw = item.get("contract")
        quote_raw = item.get("quote")
        contract_payload: dict[str, Any] = (
            contract_raw if isinstance(contract_raw, dict) else item
        )
        quote_payload: dict[str, Any] = quote_raw if isinstance(quote_raw, dict) else item
        if "underlying" not in contract_payload:
            contract_payload = {**contract_payload, "underlying": underlying}
        contract = parse_contract(contract_payload)
        quote = parse_quote(quote_payload)
        entries.append(ChainEntry(contract=contract, quote=quote))
    return OptionChain(
        underlying=underlying,
        underlying_price=underlying_price,
        as_of=as_of,
        entries=tuple(entries),
        source=str(payload.get("source") or "fixture"),
    )


def load_chain_json(path: str | Path) -> OptionChain:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("chain JSON root must be an object")
    return parse_chain_payload(data)
