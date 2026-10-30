# Operations

## Modes

| Mode | Orders |
|---|---|
| `research_only` | Never |
| `paper_dry_run` (default) | Persist proposals / READY only; never submit |
| `paper_auto` | Paper submit after gates + resolved §21 limits + `auto_paper_orders_enabled` |

## Kill switch

Set `TRADING_PAUSED=true` (Compose default). Blocks new order submission and trading-day recovery submits; **research, reconciliation, reporting, and alerts continue**.

## Scheduler (M5)

Idempotent jobs under a single SQLite worker lock. Period keys prevent duplicate completed runs (use `--force` to override).

| Job | Cadence | Behavior |
|---|---|---|
| `startup` | Each boot / daily | Verify paper, schema, secrets presence, clock, capabilities; reconcile; expire stale approvals |
| `trading_day` | Exchange trading days | Reconcile, open orders, assignment/expiry + dividend alerts, drift; skip holidays; `skipped_paused` when kill switch on |
| `monthly` | Calendar month | Research refresh hook, 100-share lot scan, optional chain evaluate, partial accumulation notes, monthly JSON report |
| `event_review` | On demand | Focused review for fills / corp actions / outages / mismatches |

Internal timestamps are UTC; display TZ `America/Denver` is fine for logs. Holiday calendar is NYSE-oriented.

```bash
dividend-agent scheduler run --jobs startup,trading_day \
  --config /config/strategy.yaml --db /data/agent.sqlite3 --reports /reports
dividend-agent scheduler run --jobs monthly --period 2026-10 \
  --db /data/agent.sqlite3 --reports /reports --chain /data/chain.json
dividend-agent scheduler run --jobs event_review --event fill:client-order-id \
  --db /data/agent.sqlite3 --reports /reports
dividend-agent scheduler status --db /data/agent.sqlite3
```

Reports and alerts are written under `--reports` (e.g. `monthly-report-YYYY-MM.json`, `alert-*.json`) and mirrored in SQLite `notifications`.

## Common commands

```bash
dividend-agent config validate /config/strategy.yaml
dividend-agent broker verify-paper
dividend-agent broker reconcile --db /data/agent.sqlite3
dividend-agent broker status --db /data/agent.sqlite3
dividend-agent ledger migrate --db /data/agent.sqlite3
dividend-agent options evaluate chain.json --set-liquidity-guardrails
dividend-agent trade status --db /data/agent.sqlite3
dividend-agent trade recover <client_order_id> --db /data/agent.sqlite3
dividend-agent approvals list --db /data/agent.sqlite3
dividend-agent approvals approve <approval_id> --db /data/agent.sqlite3
dividend-agent approvals reject <approval_id> --db /data/agent.sqlite3
dividend-agent scheduler run --jobs startup,trading_day --db /data/agent.sqlite3 --reports /reports
dividend-agent health
```

## Paper execution (M4)

- Every submit path verifies paper, checks kill switch, and requires resolved null guardrails.
- Client order IDs are stable (`da-<intent_uuid_hex>`). After timeout, the manager **queries by client order ID** before any retry.
- Hard blocks (live destination, uncovered, strike below WAC, etc.) are never approvable.
- Exception approvals expire, can be superseded, and must revalidate before submit.

## Cadence

Startup verify → trading-day drift checks → monthly research/call cycle → event-driven reviews → monthly report. Exceptions pause trading safely; reporting continues.

## Backups

See `scripts/backup.sh` (placeholder) and TrueNAS volume guidance.
