# Architecture

## Overview

Modular Python application (`src/dividend_agent/`) with:

- **Config** — validated YAML; default `paper_dry_run`
- **Brokers** — paper-only adapters; M1 REST (`AlpacaPaperAdapter`) with MCP preferred later; fail closed without positive paper verification
- **Research** — SEC EDGAR + company facts + dividend labels + screener + cited memos (M2)
- **Options / strategy** — chains, probability, hard gates (M3)
- **Execution** — reconciliation (M1, read-only); idempotent paper orders + approval queue (M4); null guardrails block auto-submit
- **Storage** — SQLite WAL + migrations (M1)
- **Scheduling / reporting** — unattended cadence (M5)
- **Deploy** — Docker Compose on TrueNAS SCALE (M6)

```text
Research (read-only) → Policy gates → TradeIntent → Validator → dry-run log | paper submit
                              ↑
                     null limits / unverified paper → fail closed
```

## Milestones

See product spec §20: M0 skeleton → M1 broker/ledger → M2 research → M3 options → M4 execution → M5 scheduler → M6 TrueNAS.

## ADRs

Decisions that change boundaries live under [ADR/](ADR/).
