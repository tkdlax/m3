# ADR 0003: M1 uses Alpaca paper REST; MCP remains preferred later

## Status

Accepted

## Context

Spec §7.1 prefers Alpaca MCP after capability verification, with a narrowly scoped paper SDK/REST fallback behind the same interface. M1 must verify paper and record broker state without requiring an MCP client runtime.

## Decision

Implement `AlpacaPaperAdapter` over the Alpaca Trading REST API for paper hosts only. Keep `AlpacaMcpAdapter` as a stub that fails closed. Document MCP as the preferred future tool surface once discovery confirms unattended coverage.

## Consequences

Operators can reconcile with paper API keys today. MCP wiring must map onto the same `BrokerAdapter` and retain fail-closed paper attestation. No order submission in either adapter for M1.
