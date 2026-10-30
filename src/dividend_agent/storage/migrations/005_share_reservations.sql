-- Milestone A: atomic share reservations for covered-call eligibility.

CREATE TABLE IF NOT EXISTS share_reservations (
    reservation_id TEXT PRIMARY KEY,
    client_order_id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    underlying TEXT NOT NULL,
    shares TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (client_order_id) REFERENCES local_orders(client_order_id)
);

CREATE INDEX IF NOT EXISTS idx_share_reservations_active
    ON share_reservations(account_id, underlying, status);
