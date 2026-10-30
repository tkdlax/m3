# Alpaca integration

## Policy

- **Paper only** in v1. No live endpoints, live credentials, or live-trading toggle.
- Prefer the official **Alpaca MCP** server once capability names/schemas are verified for unattended paper operation (spec §7.1).
- **M1 implements a narrowly scoped paper REST adapter** (`AlpacaPaperAdapter`) behind `BrokerAdapter` so verify + ledger work without an MCP runtime.
- No silent paper/live switching. `ALPACA_PAPER_BASE_URL` must be a paper host; `https://api.alpaca.markets` is rejected.
- Unverified paper → fail closed; dependent workflows (reconcile) refuse to run.

## M1 status

| Capability | Status |
|---|---|
| `verify_paper` | Implemented (REST `GET /v2/account` + paper base URL attestation) |
| `get_account` | Implemented |
| `list_positions` | Implemented |
| `list_orders` | Implemented (read-only) |
| `list_activities` | Implemented (read-only) |
| Capability discovery | Static M1 read set reported after verify |
| Order submit / cancel | **Not implemented** (M4) |
| Market data / option chains | Later (M3) |
| Alpaca MCP adapter | Stub — prefer when discovery confirms coverage |

## Credentials

```bash
ALPACA_PAPER_API_KEY=...
ALPACA_PAPER_API_SECRET=...
ALPACA_PAPER_BASE_URL=https://paper-api.alpaca.markets
```

See `.env.example`. Never commit secrets. Do not place live keys on the host for v1.

Without both key and secret, `create_broker_adapter()` returns `UnconfiguredBrokerAdapter` and all verify/read paths fail closed.

## CLI

```bash
dividend-agent broker verify-paper
dividend-agent broker reconcile --config config/strategy.example.yaml --db ./data/agent.sqlite3
dividend-agent broker status --db ./data/agent.sqlite3
dividend-agent ledger migrate --db ./data/agent.sqlite3
```

Reconcile pulls account, positions, orders, and activities into SQLite (WAL) and writes audit/`job_runs` rows. **No orders are submitted.**

## MCP preference (later)

When wiring `AlpacaMcpAdapter`, re-verify official MCP tools against current Alpaca docs, map them onto `BrokerAdapter`, and keep the same paper attestation + fail-closed rules. REST remains a tested fallback behind the same interface.
