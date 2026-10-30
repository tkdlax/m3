# Research sources

## SEC EDGAR (M2)

- **Tickers:** `https://www.sec.gov/files/company_tickers.json`
- **Submissions (filings):** `https://data.sec.gov/submissions/CIK##########.json`
- **Company facts (XBRL):** `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
- **Archive URLs:** `https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/…`

Requirements:

- Descriptive **User-Agent** with contact (`research.sec_user_agent` / `SEC_USER_AGENT`).
- Client-side **caching** (`--cache-dir`) and polite **rate limiting**.
- Citations include company/CIK, accession, form, filing date, report period, URL, and fact name when applicable.
- **Never fabricate** missing filings or facts — missing data fails closed or flags review.

## Dividends

- Declared per-share amounts may be taken from company facts tags when present (`CommonStockDividendsPerShareDeclared`), labeled **`declared`**.
- Trailing yield uses summed declared events and is labeled **`trailing`**.
- Annualized-from-latest-declared yield is labeled **`forecast`** (not company guidance) and recorded as an unknown/caveat.
- Ex-dividend calendars, optionability, and live prices are **not** supplied by SEC alone — pass market inputs from broker/licensed feeds (CLI flags / future adapters).

## Market inputs

Price, 52-week range, liquidity, and optionability must come from authorized market/broker data. The screener fails closed or marks **review** when those inputs are absent.

## AI research provider

`ResearchAgent` is provider-neutral (`DeterministicResearchAgent` default; `LlmResearchAgentStub` optional). Agents receive structured data and untrusted excerpts only — **no order-submission credentials or tools**. LLM output (when enabled later) must be typed claims with citations and independently validated.

## CLI

```bash
dividend-agent research symbol GIS --config config/strategy.example.yaml \
  --price 40 --week52-high 50 --week52-low 30 \
  --avg-dollar-volume 50000000 --optionable \
  --db ./data/agent.sqlite3 --cache-dir .cache/sec

dividend-agent research screen GIS --trailing-yield 0.06 --price 40 \
  --week52-high 50 --week52-low 30 --avg-dollar-volume 50000000 --optionable
```
