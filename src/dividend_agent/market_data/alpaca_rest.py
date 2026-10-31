"""Alpaca Market Data REST adapter (read-only).

Uses the official data host ``data.alpaca.markets`` with paper API keys.
Trading-host option contract metadata comes from ``paper-api.alpaca.markets``.
No order submission methods are implemented.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from dividend_agent.brokers.credentials import AlpacaPaperCredentials, assert_paper_base_url
from dividend_agent.market_data.freshness import assert_dividend_entry_complete
from dividend_agent.market_data.interface import (
    MARKET_DATA_READ_CAPABILITIES,
    MarketDataAdapter,
    MarketDataAuthError,
    MarketDataError,
)
from dividend_agent.market_data.occ import parse_occ_symbol
from dividend_agent.market_data.types import (
    DividendCalendarEntry,
    DividendCalendarResult,
    StockSnapshot,
)
from dividend_agent.options.chains import ChainEntry, OptionChain
from dividend_agent.options.contract_spec import OptionContract, OptionQuote, OptionRight

DEFAULT_DATA_BASE_URL = "https://data.alpaca.markets"
CANONICAL_DATA_HOST = "data.alpaca.markets"


def assert_data_base_url(base_url: str) -> str:
    """Allow only the Alpaca market-data host (not trading/live)."""
    from urllib.parse import urlparse

    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme.lower() != "https":
        raise MarketDataError(f"market-data URL must use https; got {normalized!r}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise MarketDataError(f"market-data URL must be a bare https host: {normalized!r}")
    host = (parsed.hostname or "").lower()
    if host != CANONICAL_DATA_HOST:
        raise MarketDataError(
            f"market-data URL host must be {CANONICAL_DATA_HOST}; got {normalized!r}"
        )
    if parsed.port is not None and parsed.port != 443:
        raise MarketDataError(f"market-data URL unexpected port: {normalized!r}")
    if parsed.path not in {"", "/"}:
        raise MarketDataError(f"market-data URL unexpected path: {normalized!r}")
    return f"https://{CANONICAL_DATA_HOST}"


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


class AlpacaMarketDataRestAdapter(MarketDataAdapter):
    """Read-only Alpaca data + paper trading metadata client."""

    def __init__(
        self,
        credentials: AlpacaPaperCredentials,
        *,
        data_base_url: str = DEFAULT_DATA_BASE_URL,
        data_client: httpx.Client | None = None,
        trading_client: httpx.Client | None = None,
        timeout: float = 30.0,
        option_feed: str = "indicative",
    ) -> None:
        self._credentials = credentials
        self._data_base = assert_data_base_url(data_base_url)
        self._trading_base = assert_paper_base_url(credentials.base_url)
        self._option_feed = option_feed
        headers = {
            "APCA-API-KEY-ID": credentials.api_key,
            "APCA-API-SECRET-KEY": credentials.api_secret,
            "Accept": "application/json",
        }
        self._owns_data = data_client is None
        self._owns_trading = trading_client is None
        self._data = data_client or httpx.Client(
            base_url=self._data_base, headers=headers, timeout=timeout
        )
        self._trading = trading_client or httpx.Client(
            base_url=self._trading_base, headers=headers, timeout=timeout
        )
        self._capabilities: tuple[str, ...] | None = None

    def close(self) -> None:
        if self._owns_data:
            self._data.close()
        if self._owns_trading:
            self._trading.close()

    def __enter__(self) -> AlpacaMarketDataRestAdapter:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(self, client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise MarketDataError(f"Alpaca market-data request failed: {exc}") from exc
        if response.status_code in {401, 403}:
            raise MarketDataAuthError(
                f"Alpaca market-data authentication failed ({response.status_code}); "
                "refusing market-data reads and orders that depend on them"
            )
        if response.status_code >= 400:
            detail = response.text[:500]
            raise MarketDataError(
                f"Alpaca market-data HTTP {response.status_code} for {path}: {detail}"
            )
        if not response.content:
            return None
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise MarketDataError(f"invalid JSON from Alpaca market-data {path}") from exc

    def discover_capabilities(self) -> tuple[str, ...]:
        if self._capabilities is not None:
            return self._capabilities
        # Lightweight auth probe against data host clock/endpoint.
        try:
            self._request(self._data, "GET", "/v2/stocks/AAPL/snapshot")
        except MarketDataAuthError:
            self._capabilities = ()
            raise
        except MarketDataError:
            # Endpoint/schema issues still mean we cannot claim readiness.
            self._capabilities = ()
            return self._capabilities
        self._capabilities = MARKET_DATA_READ_CAPABILITIES
        return self._capabilities

    def get_stock_snapshot(self, symbol: str) -> StockSnapshot:
        sym = symbol.strip().upper()
        payload = self._request(self._data, "GET", f"/v2/stocks/{sym}/snapshot")
        if not isinstance(payload, dict):
            raise MarketDataError(f"unexpected stock snapshot payload for {sym}")
        latest_trade = payload.get("latestTrade") or payload.get("latest_trade") or {}
        latest_quote = payload.get("latestQuote") or payload.get("latest_quote") or {}
        daily = payload.get("dailyBar") or payload.get("daily_bar") or {}
        prev = payload.get("prevDailyBar") or payload.get("prev_daily_bar") or {}
        price = _dec(latest_trade.get("p") if isinstance(latest_trade, dict) else None)
        bid = _dec(latest_quote.get("bp") if isinstance(latest_quote, dict) else None)
        ask = _dec(latest_quote.get("ap") if isinstance(latest_quote, dict) else None)
        observed = (
            _parse_dt(latest_quote.get("t") if isinstance(latest_quote, dict) else None)
            or _parse_dt(latest_trade.get("t") if isinstance(latest_trade, dict) else None)
            or datetime.now(UTC)
        )
        # 52-week fields are not always on snapshot; leave None rather than invent.
        high = _dec(daily.get("h") if isinstance(daily, dict) else None) or _dec(
            prev.get("h") if isinstance(prev, dict) else None
        )
        low = _dec(daily.get("l") if isinstance(daily, dict) else None) or _dec(
            prev.get("l") if isinstance(prev, dict) else None
        )
        return StockSnapshot(
            symbol=sym,
            price=price or bid or ask,
            bid=bid,
            ask=ask,
            week52_high=high,
            week52_low=low,
            avg_dollar_volume=None,
            optionable=None,
            observed_at=observed,
            source="alpaca_data_rest",
            raw=payload,
        )

    def get_option_chain(
        self,
        underlying: str,
        *,
        expiration_gte: date | None = None,
        expiration_lte: date | None = None,
        as_of: datetime | None = None,
    ) -> OptionChain:
        und = underlying.strip().upper()
        today = (as_of or datetime.now(UTC)).date()
        gte = expiration_gte or today
        lte = expiration_lte or (today + timedelta(days=60))

        contracts = self._list_option_contracts(und, expiration_gte=gte, expiration_lte=lte)
        snapshots = self._option_chain_snapshots(und, expiration_gte=gte, expiration_lte=lte)

        # Underlying price from equity snapshot (fail closed if unavailable).
        equity = self.get_stock_snapshot(und)
        underlying_price = equity.price

        entries: list[ChainEntry] = []
        for symbol, contract in contracts.items():
            if contract.right != OptionRight.CALL:
                continue
            snap = snapshots.get(symbol) or {}
            quote = self._quote_from_option_snapshot(snap)
            entries.append(ChainEntry(contract=contract, quote=quote))

        # Snapshots may include contracts not returned by contracts endpoint.
        for symbol, snap in snapshots.items():
            if symbol in contracts:
                continue
            parsed = parse_occ_symbol(symbol)
            if parsed is None or parsed["right"] != OptionRight.CALL:
                continue
            contract = OptionContract(
                symbol=symbol,
                underlying=und,
                expiration=parsed["expiration"],  # type: ignore[arg-type]
                strike=parsed["strike"],  # type: ignore[arg-type]
                right=OptionRight.CALL,
            )
            entries.append(
                ChainEntry(contract=contract, quote=self._quote_from_option_snapshot(snap))
            )

        observed = as_of or datetime.now(UTC)
        return OptionChain(
            underlying=und,
            underlying_price=underlying_price,
            as_of=observed.date(),
            entries=tuple(entries),
            source="alpaca_data_rest",
        )

    def _list_option_contracts(
        self,
        underlying: str,
        *,
        expiration_gte: date,
        expiration_lte: date,
    ) -> dict[str, OptionContract]:
        params: dict[str, Any] = {
            "underlying_symbols": underlying,
            "status": "active",
            "type": "call",
            "expiration_date_gte": expiration_gte.isoformat(),
            "expiration_date_lte": expiration_lte.isoformat(),
            "limit": 1000,
        }
        payload = self._request(self._trading, "GET", "/v2/options/contracts", params=params)
        rows: list[Any] = []
        if isinstance(payload, dict):
            raw_rows = payload.get("option_contracts") or payload.get("contracts") or []
            if isinstance(raw_rows, list):
                rows = raw_rows
        elif isinstance(payload, list):
            rows = payload
        out: dict[str, OptionContract] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            exp = date.fromisoformat(str(row.get("expiration_date") or row.get("expiration"))[:10])
            deliverables = row.get("deliverables") or []
            deliverable_shares = 100
            if isinstance(deliverables, list) and deliverables:
                qty = 0
                for d in deliverables:
                    if isinstance(d, dict) and str(d.get("type") or "").lower() in {
                        "equity",
                        "share",
                        "stock",
                    }:
                        qty += int(Decimal(str(d.get("amount") or d.get("quantity") or 0)))
                if qty > 0:
                    deliverable_shares = qty
            multiplier = int(row.get("multiplier") or 100)
            adjusted = bool(row.get("style") == "adjusted") or deliverable_shares != 100
            out[symbol] = OptionContract(
                symbol=symbol,
                underlying=underlying,
                expiration=exp,
                strike=Decimal(str(row["strike_price"])),
                right=OptionRight.CALL,
                multiplier=multiplier,
                deliverable_shares=deliverable_shares,
                adjusted=adjusted,
                tradable=str(row.get("status") or "active").lower() == "active",
                status=str(row.get("status") or "active"),
                instrument_id=str(row["id"]) if row.get("id") else None,
                raw=row,
            )
        return out

    def _option_chain_snapshots(
        self,
        underlying: str,
        *,
        expiration_gte: date,
        expiration_lte: date,
    ) -> dict[str, dict[str, Any]]:
        params: dict[str, Any] = {
            "feed": self._option_feed,
            "type": "call",
            "expiration_date_gte": expiration_gte.isoformat(),
            "expiration_date_lte": expiration_lte.isoformat(),
            "limit": 1000,
        }
        out: dict[str, dict[str, Any]] = {}
        page_token: str | None = None
        for _ in range(20):
            page_params = dict(params)
            if page_token:
                page_params["page_token"] = page_token
            payload = self._request(
                self._data,
                "GET",
                f"/v1beta1/options/snapshots/{underlying}",
                params=page_params,
            )
            if not isinstance(payload, dict):
                break
            snaps = payload.get("snapshots") or {}
            if isinstance(snaps, dict):
                for sym, body in snaps.items():
                    if isinstance(body, dict):
                        out[str(sym).upper()] = body
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        return out

    def _quote_from_option_snapshot(self, snap: dict[str, Any]) -> OptionQuote:
        latest_quote = snap.get("latestQuote") or snap.get("latest_quote") or {}
        latest_trade = snap.get("latestTrade") or snap.get("latest_trade") or {}
        greeks = snap.get("greeks") or {}
        bid = _dec(latest_quote.get("bp") if isinstance(latest_quote, dict) else None)
        ask = _dec(latest_quote.get("ap") if isinstance(latest_quote, dict) else None)
        last = _dec(latest_trade.get("p") if isinstance(latest_trade, dict) else None)
        iv = _dec(snap.get("impliedVolatility") or snap.get("implied_volatility"))
        if iv is None and isinstance(greeks, dict):
            iv = _dec(greeks.get("implied_volatility") or greeks.get("iv"))
        observed = _parse_dt(
            latest_quote.get("t") if isinstance(latest_quote, dict) else None
        ) or _parse_dt(latest_trade.get("t") if isinstance(latest_trade, dict) else None)
        return OptionQuote(
            bid=bid,
            ask=ask,
            last=last,
            bid_size=int(latest_quote["bs"])
            if isinstance(latest_quote, dict) and latest_quote.get("bs") is not None
            else None,
            ask_size=int(latest_quote["as"])
            if isinstance(latest_quote, dict) and latest_quote.get("as") is not None
            else None,
            volume=None,
            open_interest=(
                int(oi)
                if (oi := snap.get("openInterest") or snap.get("open_interest")) is not None
                else None
            ),
            implied_volatility=iv,
            quote_as_of=observed,
        )

    def get_dividend_calendar(
        self,
        symbols: tuple[str, ...] | list[str],
        *,
        window_start: date,
        window_end: date,
    ) -> DividendCalendarResult:
        syms = tuple(s.strip().upper() for s in symbols if s and s.strip())
        if not syms:
            raise MarketDataError("dividend calendar requires at least one symbol")
        # process_date filter can lag ex_date — widen end by 14 days then filter locally.
        params = {
            "symbols": ",".join(syms),
            "types": "cash_dividend,stock_dividend",
            "start": window_start.isoformat(),
            "end": (window_end + timedelta(days=14)).isoformat(),
            "data_quality": "complete",
            "limit": 1000,
        }
        observed_at = datetime.now(UTC)
        payload = self._request(self._data, "GET", "/v1/corporate-actions", params=params)
        entries: list[DividendCalendarEntry] = []
        rows: list[Any] = []
        if isinstance(payload, dict):
            # Shape may nest by type: { "cash_dividends": [...] } or flat "corporate_actions"
            for key in ("cash_dividends", "stock_dividends", "corporate_actions", "data"):
                block = payload.get(key)
                if isinstance(block, list):
                    rows.extend(block)
            if not rows:
                # Some responses are { "AAPL": { "cash_dividends": [...] } }
                for sym in syms:
                    block = payload.get(sym)
                    if isinstance(block, dict):
                        for key in ("cash_dividends", "stock_dividends"):
                            sub = block.get(key)
                            if isinstance(sub, list):
                                for item in sub:
                                    if isinstance(item, dict):
                                        rows.append({**item, "symbol": item.get("symbol") or sym})
        elif isinstance(payload, list):
            rows = payload

        for row in rows:
            if not isinstance(row, dict):
                continue
            ex_raw = row.get("ex_date") or row.get("exDate")
            if not ex_raw:
                # Incomplete — reject from usable calendar (fail closed for that row).
                continue
            ex_date = date.fromisoformat(str(ex_raw)[:10])
            if ex_date < window_start or ex_date > window_end:
                continue
            symbol = str(
                row.get("symbol")
                or row.get("ticker")
                or row.get("initiating_symbol")
                or (syms[0] if len(syms) == 1 else "")
            ).upper()
            if not symbol or symbol not in syms:
                continue
            entry = DividendCalendarEntry(
                symbol=symbol,
                ex_date=ex_date,
                record_date=_date_opt(row.get("record_date") or row.get("recordDate")),
                payable_date=_date_opt(row.get("payable_date") or row.get("payableDate")),
                declaration_date=_date_opt(
                    row.get("declaration_date") or row.get("declarationDate")
                ),
                amount=_dec(row.get("rate") or row.get("cash") or row.get("amount")),
                action_type=str(row.get("type") or row.get("ca_type") or "cash_dividend"),
                source="alpaca_corporate_actions",
                observed_at=observed_at,
                raw=row,
            )
            assert_dividend_entry_complete(entry)
            entries.append(entry)

        return DividendCalendarResult(
            symbols=syms,
            entries=tuple(entries),
            source="alpaca_corporate_actions",
            observed_at=observed_at,
            window_start=window_start,
            window_end=window_end,
        )


def _date_opt(value: Any) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])
