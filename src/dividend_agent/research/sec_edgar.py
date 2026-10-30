"""SEC EDGAR client: submissions, tickers, caching, rate limits, citations."""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from dividend_agent.research.citations import Citation

SEC_DATA_BASE = "https://data.sec.gov"
SEC_WWW_BASE = "https://www.sec.gov"
DEFAULT_MIN_INTERVAL_SECONDS = 0.2  # ~5 req/s polite ceiling for SEC guidance


class SecEdgarError(RuntimeError):
    """SEC client / data error (fail closed)."""


class SecEdgarClient:
    """Official EDGAR data.sec.gov + www.sec.gov helpers.

    Requires a descriptive User-Agent with contact (SEC fair-access guidance).
    Never fabricates filings or facts.
    """

    def __init__(
        self,
        user_agent: str,
        *,
        cache_dir: str | Path | None = None,
        client: httpx.Client | None = None,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        timeout: float = 30.0,
    ) -> None:
        if not user_agent or not user_agent.strip():
            raise SecEdgarError(
                "SEC User-Agent is required (set research.sec_user_agent / SEC_USER_AGENT)"
            )
        self._user_agent = user_agent.strip()
        self._cache_dir = Path(cache_dir) if cache_dir else None
        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._min_interval = max(0.0, min_interval_seconds)
        self._last_request_at = 0.0
        self._owns_client = client is None
        self._client = client or httpx.Client(
            headers={
                "User-Agent": self._user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> SecEdgarClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _cache_path(self, key: str) -> Path | None:
        if self._cache_dir is None:
            return None
        safe = key.replace("/", "_").replace(":", "_")
        return self._cache_dir / f"{safe}.json"

    def _get_json(self, url: str, *, cache_key: str | None = None) -> Any:
        path = self._cache_path(cache_key) if cache_key else None
        if path is not None and path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))

        self._throttle()
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            raise SecEdgarError(f"SEC request failed: {exc}") from exc
        finally:
            self._last_request_at = time.monotonic()

        if response.status_code == 404:
            raise SecEdgarError(f"SEC resource not found: {url}")
        if response.status_code == 403:
            raise SecEdgarError(
                "SEC returned 403 — check User-Agent identification and rate limits"
            )
        if response.status_code >= 400:
            raise SecEdgarError(f"SEC HTTP {response.status_code} for {url}")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise SecEdgarError(f"invalid JSON from SEC {url}") from exc
        if path is not None:
            path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def load_ticker_map(self) -> dict[str, str]:
        """Return uppercase ticker -> zero-padded 10-digit CIK."""
        url = f"{SEC_WWW_BASE}/files/company_tickers.json"
        payload = self._get_json(url, cache_key="company_tickers")
        if not isinstance(payload, dict):
            raise SecEdgarError("unexpected company_tickers payload")
        mapping: dict[str, str] = {}
        for item in payload.values():
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker") or "").upper()
            cik_raw = item.get("cik_str")
            if not ticker or cik_raw is None:
                continue
            mapping[ticker] = str(cik_raw).zfill(10)
        return mapping

    def resolve_cik(self, ticker: str) -> str:
        ticker_u = ticker.strip().upper()
        mapping = self.load_ticker_map()
        cik = mapping.get(ticker_u)
        if not cik:
            raise SecEdgarError(f"CIK not found for ticker {ticker_u!r}")
        return cik

    def get_submissions(self, cik: str) -> dict[str, Any]:
        padded = str(cik).zfill(10)
        url = f"{SEC_DATA_BASE}/submissions/CIK{padded}.json"
        payload = self._get_json(url, cache_key=f"submissions_CIK{padded}")
        if not isinstance(payload, dict):
            raise SecEdgarError("unexpected submissions payload")
        return payload

    def get_company_facts(self, cik: str) -> dict[str, Any]:
        padded = str(cik).zfill(10)
        url = f"{SEC_DATA_BASE}/api/xbrl/companyfacts/CIK{padded}.json"
        payload = self._get_json(url, cache_key=f"companyfacts_CIK{padded}")
        if not isinstance(payload, dict):
            raise SecEdgarError("unexpected companyfacts payload")
        return payload

    @staticmethod
    def filing_archive_url(cik: str, accession: str, primary_document: str | None = None) -> str:
        cik_num = str(int(str(cik)))  # strip leading zeros for archives path
        acc_nodash = accession.replace("-", "")
        base = f"{SEC_WWW_BASE}/Archives/edgar/data/{cik_num}/{acc_nodash}"
        if primary_document:
            return f"{base}/{primary_document}"
        return f"{base}/"

    def list_recent_filings(
        self,
        cik: str,
        *,
        forms: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
        limit: int = 20,
    ) -> list[Citation]:
        """Return filing citations from submissions recent filings."""
        submissions = self.get_submissions(cik)
        recent = submissions.get("filings", {}).get("recent", {})
        if not isinstance(recent, dict):
            raise SecEdgarError("submissions missing filings.recent")
        forms_list = recent.get("form") or []
        accessions = recent.get("accessionNumber") or []
        filing_dates = recent.get("filingDate") or []
        report_dates = recent.get("reportDate") or []
        primary_docs = recent.get("primaryDocument") or []
        retrieved = datetime.now(UTC)
        out: list[Citation] = []
        for i, form in enumerate(forms_list):
            if form not in forms:
                continue
            accession = str(accessions[i]) if i < len(accessions) else ""
            if not accession:
                continue
            primary = str(primary_docs[i]) if i < len(primary_docs) else None
            filing_date = _parse_date(filing_dates[i] if i < len(filing_dates) else None)
            report_period = _parse_date(report_dates[i] if i < len(report_dates) else None)
            out.append(
                Citation(
                    source="sec_edgar",
                    cik=str(cik).zfill(10),
                    accession=accession,
                    form=str(form),
                    filing_date=filing_date,
                    report_period=report_period,
                    url=self.filing_archive_url(cik, accession, primary),
                    retrieved_at=retrieved,
                )
            )
            if len(out) >= limit:
                break
        return out

    def latest_filing(self, cik: str, form: str) -> Citation | None:
        filings = self.list_recent_filings(cik, forms=(form,), limit=1)
        return filings[0] if filings else None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
