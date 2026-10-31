-- Milestone D: supervised paper-cycle state (restart-safe stages).

CREATE TABLE IF NOT EXISTS paper_cycles (
    cycle_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    account_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    research_decision TEXT,
    memo_id TEXT,
    stock_client_order_id TEXT,
    call_client_order_id TEXT,
    target_shares TEXT NOT NULL DEFAULT '100',
    notes_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_cycles_symbol_status
    ON paper_cycles(symbol, status);

CREATE TABLE IF NOT EXISTS cycle_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    decision TEXT NOT NULL,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    outcome TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (cycle_id) REFERENCES paper_cycles(cycle_id)
);

CREATE INDEX IF NOT EXISTS idx_cycle_decisions_cycle ON cycle_decisions(cycle_id);
