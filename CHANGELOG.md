# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- M5 autonomous scheduler: startup/trading-day/monthly/event jobs, NYSE holiday
  skip, worker lock, idempotent job_keys, file+SQLite alerts, monthly reports;
  TRADING_PAUSED pauses trading independently of research/reporting.
- M4 paper execution: order state machine, client-order-id idempotency, timeout
  lookup-before-retry, approval queue (hard blocks never approvable), kill switch,
  paper verify before submit, CLI trade/approvals. No live trading path.
- M3 options engine: chain parsing, contract/deliverable validation, BS terminal COP/OTM
  model, WAC/coverage/ex-div/stale-quote/liquidity hard gates, explainable TradeIntents,
  CLI `options evaluate`. No order submission. §21 liquidity nulls fail closed.
- M2 research: SEC EDGAR client (User-Agent, cache, rate limit, citations), company facts,
  declared/forecast dividend labeling, screener, deterministic memos + `ResearchAgent`,
  SQLite issuers/filings/memos, CLI `research symbol|screen`. Missing data fails closed.
- M1 broker + ledger: Alpaca paper REST adapter (`verify_paper`, account, positions, orders,
  activities), SQLite WAL migrations/repository, reconciliation into local journal, CLI
  (`broker verify-paper|reconcile|status`, `ledger migrate`). No order submission.
- M0 repository skeleton: package layout, config validation, fail-closed null guardrails,
  CLI stubs (`config validate`, `health`, `version`, `broker verify-paper`), Docker Compose,
  CI, and documentation stubs. Default mode `paper_dry_run`. No live trading path.
