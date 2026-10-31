# ADR 0004: Milestone B market data via Alpaca REST (MCP deferred)

## Status

Accepted

## Context

Handoff Priority 1 §5 requires unattended stock quotes, option chains/contracts,
IV/liquidity fields, and a dividend calendar with source/timestamps. Spec §7.1
and ADR 0003 prefer Alpaca MCP once capability discovery confirms unattended
coverage. At Milestone B implementation time:

- `AlpacaMcpAdapter` remains an unverified stub with empty capabilities.
- Official Alpaca **Market Data REST** documents stock snapshots
  (`GET /v2/stocks/{symbol}/snapshot`), option chain snapshots
  (`GET /v1beta1/options/snapshots/{underlying}`), corporate actions
  (`GET /v1/corporate-actions`), and trading-host option contracts
  (`GET /v2/options/contracts`) authenticated with the same paper API keys.
- Unattended MCP tool schemas for these reads were not confirmed for production
  use without an interactive MCP host.

## Decision

1. Implement a typed read-only `MarketDataAdapter` port and ship
   `AlpacaMarketDataRestAdapter` against `https://data.alpaca.markets` (plus
   paper trading host for contract metadata).
2. Keep Alpaca MCP deferred for market data until discovery proves unattended
   read schemas; when added, it must map onto the same interface and remain
   **read-only** (no submit/cancel tools in the market-data or agent session).
3. Fixture JSON chains stay available for **replay/testing only**. Production
   scheduler/CLI live paths must fetch chains and calendars; `source=fixture`
   is rejected by freshness checks on the live path.
4. Missing capabilities or expired auth raise explicit errors and produce **no
   orders** (evaluation skips / fail closed).

## Consequences

- Operators need paper API keys that are entitled for market data (indicative
  feed is the default via `ALPACA_OPTION_FEED=indicative`).
- Optional `ALPACA_DATA_BASE_URL` must be exactly `https://data.alpaca.markets`.
- MCP control-plane PR #10 (CLI-only agent tools) remains complementary and
  must not introduce brokerage write tools.
