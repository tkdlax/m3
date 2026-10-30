-- M1 initial ledger schema (SQLite). Broker-authoritative snapshots + audit.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    cash TEXT NOT NULL,
    buying_power TEXT NOT NULL,
    equity TEXT NOT NULL,
    portfolio_value TEXT NOT NULL,
    pattern_day_trader INTEGER NOT NULL DEFAULT 0,
    trading_blocked INTEGER NOT NULL DEFAULT 0,
    account_blocked INTEGER NOT NULL DEFAULT 0,
    options_approved_level INTEGER,
    raw_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL DEFAULT 'us_equity',
    qty TEXT NOT NULL,
    qty_available TEXT,
    avg_entry_price TEXT NOT NULL,
    market_value TEXT,
    cost_basis TEXT,
    side TEXT NOT NULL DEFAULT 'long',
    asset_id TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    UNIQUE (account_id, symbol, asset_class)
);

CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    qty TEXT NOT NULL,
    cost_per_share TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'broker_avg_entry',
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    broker_order_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    client_order_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    status TEXT NOT NULL,
    qty TEXT,
    filled_qty TEXT,
    limit_price TEXT,
    filled_avg_price TEXT,
    asset_class TEXT,
    submitted_at TEXT,
    updated_at TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
    activity_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    symbol TEXT,
    side TEXT,
    qty TEXT,
    price TEXT,
    net_amount TEXT,
    transaction_time TEXT,
    order_id TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    detail TEXT NOT NULL DEFAULT '',
    account_id TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    account_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    correlation_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_holdings_account ON holdings(account_id);
CREATE INDEX IF NOT EXISTS idx_orders_account ON orders(account_id);
CREATE INDEX IF NOT EXISTS idx_activities_account ON activities(account_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at);
