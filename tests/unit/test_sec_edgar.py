"""SEC EDGAR client tests with fixtures (known accession)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from dividend_agent.research.sec_edgar import SecEdgarClient, SecEdgarError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _client() -> SecEdgarClient:
    tickers = json.loads((FIXTURES / "sec_company_tickers.json").read_text())
    submissions = json.loads((FIXTURES / "sec_submissions_gis.json").read_text())
    facts = json.loads((FIXTURES / "sec_companyfacts_gis.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/files/company_tickers.json"):
            return httpx.Response(200, json=tickers)
        if "submissions/CIK0000051462.json" in url:
            return httpx.Response(200, json=submissions)
        if "companyfacts/CIK0000051462.json" in url:
            return httpx.Response(200, json=facts)
        return httpx.Response(404, json={"error": "missing"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return SecEdgarClient("DividendAgent/0.1 (tests@example.com)", client=http)


def test_requires_user_agent() -> None:
    with pytest.raises(SecEdgarError, match="User-Agent"):
        SecEdgarClient("")


def test_resolve_cik_and_filings() -> None:
    client = _client()
    cik = client.resolve_cik("gis")
    assert cik == "0000051462"
    filings = client.list_recent_filings(cik)
    assert filings[0].accession == "0000051462-24-000055"
    assert filings[0].form == "10-K"
    assert filings[0].url is not None
    assert "000005146224000055" in filings[0].url


def test_company_facts_roundtrip() -> None:
    client = _client()
    facts = client.get_company_facts("0000051462")
    assert facts["entityName"] == "GENERAL MILLS INC"
