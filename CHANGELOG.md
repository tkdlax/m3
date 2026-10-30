# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- M1 broker + ledger: Alpaca paper REST adapter (`verify_paper`, account, positions, orders,
  activities), SQLite WAL migrations/repository, reconciliation into local journal, CLI
  (`broker verify-paper|reconcile|status`, `ledger migrate`). No order submission.
- M0 repository skeleton: package layout, config validation, fail-closed null guardrails,
  CLI stubs (`config validate`, `health`, `version`, `broker verify-paper`), Docker Compose,
  CI, and documentation stubs. Default mode `paper_dry_run`. No live trading path.
