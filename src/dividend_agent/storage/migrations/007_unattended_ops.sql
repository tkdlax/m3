-- Milestone E: corporate-action monitoring + broker ingestion cursors.

CREATE TABLE IF NOT EXISTS corporate_action_events (
    event_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    symbol TEXT,
    qty TEXT,
    price TEXT,
    net_amount TEXT,
    transaction_time TEXT,
    broker_activity_id TEXT,
    effect TEXT NOT NULL DEFAULT '',
    handled INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cae_account_type
    ON corporate_action_events(account_id, activity_type);
CREATE INDEX IF NOT EXISTS idx_cae_handled
    ON corporate_action_events(handled);

CREATE TABLE IF NOT EXISTS ingestion_cursors (
    cursor_name TEXT PRIMARY KEY,
    cursor_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
