-- M2 research ledger: issuers, filings, memos, screening decisions.

CREATE TABLE IF NOT EXISTS issuers (
    cik TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    entity_name TEXT,
    sector TEXT,
    optionable INTEGER,
    raw_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_issuers_ticker ON issuers(ticker);

CREATE TABLE IF NOT EXISTS filings (
    accession TEXT PRIMARY KEY,
    cik TEXT NOT NULL,
    ticker TEXT,
    form TEXT NOT NULL,
    filing_date TEXT,
    report_period TEXT,
    url TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}',
    retrieved_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_filings_cik ON filings(cik);

CREATE TABLE IF NOT EXISTS research_memos (
    memo_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    cik TEXT NOT NULL,
    decision TEXT NOT NULL,
    thesis TEXT NOT NULL,
    unknowns_json TEXT NOT NULL DEFAULT '[]',
    criteria_json TEXT NOT NULL DEFAULT '[]',
    citations_json TEXT NOT NULL DEFAULT '[]',
    memo_json TEXT NOT NULL,
    agent_provider TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memos_ticker ON research_memos(ticker);

CREATE TABLE IF NOT EXISTS research_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    memo_id TEXT,
    decision TEXT NOT NULL,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (memo_id) REFERENCES research_memos(memo_id)
);
