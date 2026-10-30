-- M4 paper execution: local order lifecycle, events, approvals.

CREATE TABLE IF NOT EXISTS local_orders (
    client_order_id TEXT PRIMARY KEY,
    intent_id TEXT,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty TEXT NOT NULL,
    order_type TEXT NOT NULL,
    limit_price TEXT,
    state TEXT NOT NULL,
    broker_order_id TEXT,
    broker_status TEXT,
    filled_qty TEXT,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    hard_block INTEGER NOT NULL DEFAULT 0,
    rejection_reasons_json TEXT NOT NULL DEFAULT '[]',
    intent_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_local_orders_state ON local_orders(state);
CREATE INDEX IF NOT EXISTS idx_local_orders_broker ON local_orders(broker_order_id);

CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    detail TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_events_client ON order_events(client_order_id);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    client_order_id TEXT NOT NULL,
    approval_type TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    risk_details TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    expires_at TEXT,
    decided_at TEXT,
    decided_by TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (client_order_id) REFERENCES local_orders(client_order_id)
);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
