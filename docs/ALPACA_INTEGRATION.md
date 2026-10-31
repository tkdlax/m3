# Alpaca integration

## Policy

- **Paper only** in v1. No live endpoints, live credentials, or live-trading toggle.
- Prefer the official **Alpaca MCP** server once capability names/schemas are verified for unattended paper operation (spec §7.1).
- **REST adapter** (`AlpacaPaperAdapter`) implements verify, ledger reads, and **M4 paper submit** behind `BrokerAdapter`.
- No silent paper/live switching. `ALPACA_PAPER_BASE_URL` must be a paper host; `https://api.alpaca.markets` is rejected.
- Unverified paper → fail closed; order batches re-verify before submit.

## Capability matrix

| Capability | Status |
|---|---|
| `verify_paper` | Implemented (`GET /v2/account` + paper base URL) |
| `get_account` / positions / orders / activities | Implemented |
| `submit_paper_order` | Implemented (`POST /v2/orders`) — paper host only |
| `get_order_by_client_id` | Implemented (`GET /v2/orders:by_client_order_id`) |
| `cancel_paper_order` | Implemented (`DELETE /v2/orders/{id}`) |
| Market data / option chains | **Milestone B:** `MarketDataAdapter` + `AlpacaMarketDataRestAdapter` (`data.alpaca.markets`) |
| Dividend / ex-date calendar | `GET /v1/corporate-actions` → `ExDividendWindow` (missing ex-date rows dropped) |
| Alpaca MCP adapter | Stub — preferred when discovery confirms coverage (ADR 0004 defers MCP for market data) |

## Idempotency

Client order IDs are application-stable (`da-<intent_uuid>`). On transport timeout the order manager queries by client order ID **before** any retry to avoid duplicates.

## Credentials

```bash
ALPACA_PAPER_API_KEY=...
ALPACA_PAPER_API_SECRET=...
ALPACA_PAPER_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_BASE_URL=https://data.alpaca.markets   # optional; exact host only
ALPACA_OPTION_FEED=indicative                      # or opra when subscribed
TRADING_PAUSED=true   # kill switch; set false only when intentionally enabling submits
```

Never place live keys on the host for v1. Market-data reads reuse paper keys against the
data host; the adapter exposes **no** order-submit methods.

## CLI

```bash
dividend-agent broker verify-paper
dividend-agent broker reconcile --db ./data/agent.sqlite3
dividend-agent options evaluate --underlying GIS   # live chain (no fixture JSON)
dividend-agent options evaluate tests/fixtures/option_chain_gis.json  # replay only
dividend-agent trade status --db ./data/agent.sqlite3
dividend-agent trade recover <client_order_id> --db ./data/agent.sqlite3
dividend-agent approvals list --db ./data/agent.sqlite3
```
