# Architecture

## Overview

Modular Python application (`src/dividend_agent/`) with:

- **Config** — validated YAML; default `paper_dry_run`
- **Brokers** — paper-only adapters; M1 REST (`AlpacaPaperAdapter`) with MCP preferred later; fail closed without positive paper verification
- **Research** — SEC EDGAR + company facts + dividend labels + screener + cited memos (M2)
- **Options / strategy** — chains, probability, WAC/coverage/ex-div gates, TradeIntent selection (M3)
- **Execution** — reconciliation (M1); intent validation (M3); idempotent paper orders, approvals, kill switch (M4); Milestone A gateway: independent coverage/WAC derivation, atomic share reservations, numeric §21 limit enforcement, decision-stable client order IDs, no submit on lookup ambiguity
- **Scheduling** — idempotent startup / trading-day / monthly / event jobs, exchange calendar, atomic worker lease acquire/renew, alerts + monthly reports (M5)
- **Deploy** — TrueNAS SCALE Compose, datasets UID 10001, backup/restore, promote + emergency-stop runbook (M6)
- **Storage** — SQLite WAL + migrations (M1)
- **Scheduling / reporting** — unattended cadence (M5)
- **Deploy** — Docker Compose on TrueNAS SCALE (M6)

```text
Research (read-only) → Policy gates → TradeIntent → Gateway (OrderManager)
                              ↑                         |
                     null limits / unverified paper     +-- fresh coverage/WAC + reservations
                     / pause / non-paper URL            +-- numeric §21 limits
                                                        +-- paper submit (only path)
```

Broker write credentials stay on the gateway path. Agent overlays must not include
`ALPACA_PAPER_*` (see `execution.authority`). Open MCP control-plane PR #10 stacks
separately for out-of-process CLI-only agent tools.

## Milestones

See product spec §20: M0 skeleton → M1 broker/ledger → M2 research → M3 options → M4 execution → M5 scheduler → M6 TrueNAS.

## ADRs

Decisions that change boundaries live under [ADR/](ADR/).
