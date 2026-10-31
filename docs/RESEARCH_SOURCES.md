# Research sources

## SEC EDGAR (M2)

- **Tickers:** `https://www.sec.gov/files/company_tickers.json`
- **Submissions (filings):** `https://data.sec.gov/submissions/CIK##########.json`
- **Company facts (XBRL):** `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
- **Archive URLs:** `https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/…`

Requirements:

- Descriptive **User-Agent** with contact (`research.sec_user_agent` / `SEC_USER_AGENT`).
- Client-side **caching** with TTL (`research.sec_cache_ttl_seconds`, default 86400) and polite **rate limiting**. Stale cache entries are refetched; a new memo timestamp must not make old companyfacts appear fresh.
- Citations include company/CIK, accession, form, filing date, report period, URL, and fact name when applicable.
- **Filing narrative:** retrieve/analyze latest 10-K (and subsequent 10-Q when present). A filing **link alone does not qualify** — substantive business+risk evidence from the document body is required when `require_filing_narrative` is true.
- **Period normalization:** distinguish instant / quarterly / YTD / annual / trailing; handle amended duplicates; compose **total debt** from compatible components (same period end + kind), not tag-alternative races.
- **Never fabricate** missing filings or facts — missing data fails closed or flags review.

## Dividends

- Declared per-share amounts may be taken from company facts tags when present (`CommonStockDividendsPerShareDeclared`), labeled **`declared`**.
- Events are normalized by period/amount with overlap dedupe; **specials** (outlier amounts) are labeled and excluded from TTM.
- Trailing yield sums up to four **distinct quarterly** periods when proven; annual/unknown frequency does not pretend last-N is TTM. Labeled **`trailing`**.
- Annualized-from-latest-declared yield is labeled **`forecast`** (not company guidance) and recorded as an unknown/caveat.
- **XBRL period end is not an ex-dividend or payment date** — never invent those from companyfacts ends.
- Ex-dividend calendars, optionability, and live prices are **not** supplied by SEC alone — Milestone B pulls them via read-only `MarketDataAdapter` (Alpaca data REST / corporate actions). Fixture CLI flags remain for replay only.

## Market inputs

Price, 52-week range, liquidity, and optionability must come from authorized market/broker data. The screener fails closed or marks **review** when those inputs are absent.

## AI research provider

`ResearchAgent` is provider-neutral (`DeterministicResearchAgent` default; `LlmResearchAgentStub` optional). Structured request/response schema lives in `agents.schema` (`StructuredResearchRequest` / `StructuredResearchResponse`) with timeout/token budgets and citation validation. Agents receive structured data and untrusted excerpts only — **no order-submission credentials or tools**. Real providers are optional behind the interface; the stub validates schema without inventing claims.

## CLI

```bash
dividend-agent research symbol GIS --config config/strategy.example.yaml \
  --price 40 --week52-high 50 --week52-low 30 \
  --avg-dollar-volume 50000000 --optionable \
  --db ./data/agent.sqlite3 --cache-dir .cache/sec

dividend-agent research screen GIS --trailing-yield 0.06 --price 40 \
  --week52-high 50 --week52-low 30 --avg-dollar-volume 50000000 --optionable
```
