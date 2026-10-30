# Alpaca integration

## Policy

- **Paper only** in v1. Prefer official Alpaca MCP after verifying current capabilities at implementation time (M1).
- Optional narrowly scoped paper SDK/REST adapter behind the same `BrokerAdapter` interface.
- No silent paper/live switching. Startup must positively verify paper; otherwise dependent workflows stay disabled.
- M0 stubs (`AlpacaMcpAdapter`, `AlpacaPaperAdapter`, `UnconfiguredBrokerAdapter`) **fail closed** on `verify_paper`.

## Required capabilities (M1+)

Account/environment attestation; balances; positions; orders/fills; stock & option market data/chains; paper order submit/cancel; activities (dividends, assignments); reconciliation hooks.

## Credentials

Use paper API keys via environment (see `.env.example`). Never commit secrets. Do not place live keys on the host for v1.
