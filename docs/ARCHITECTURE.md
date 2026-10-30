# Architecture

## Overview

Modular Python application (`src/dividend_agent/`) with:

- **Config** — validated YAML; default `paper_dry_run`
- **Brokers** — paper-only adapters; M1 REST (`AlpacaPaperAdapter`) with MCP preferred later; fail closed without positive paper verification
- **Research** — SEC EDGAR + company facts + dividend labels + screener + cited memos (M2)
- **Options / strategy** — chains, probability, WAC/coverage/ex-div gates, TradeIntent selection (M3)
- **Execution** — reconciliation (M1); intent validation (M3); idempotent paper orders, approvals, kill switch (M4); null guardrails block auto-submit
- **Scheduling** — idempotent startup / trading-day / monthly / event jobs, exchange calendar, worker lock, alerts + monthly reports (M5)
- **Deploy** — TrueNAS SCALE Compose, datasets UID 10001, backup/restore, promote + emergency-stop runbook (M6)
- **Storage** — SQLite WAL + migrations (M1)
- **Scheduling / reporting** — unattended cadence (M5)
- **MCP control plane** — chat agents (Grok/Claude/Cursor) call allowlisted CLI tools only; no Alpaca secrets or order submit ([MCP_CONTROL_PLANE.md](MCP_CONTROL_PLANE.md))

```text
Chat MCP (research/jobs/approvals-by-id)
        │ docker exec / local CLI only
        ▼
Research (read-only) → Policy gates → TradeIntent → Validator → dry-run log | paper submit
                              ↑
                     null limits / unverified paper → fail closed
Alpaca MCP/REST stays inside BrokerAdapter — not exposed to chat agents.
```

## Milestones

See product spec §20: M0 skeleton → M1 broker/ledger → M2 research → M3 options → M4 execution → M5 scheduler → M6 TrueNAS.

## ADRs

Decisions that change boundaries live under [ADR/](ADR/).
