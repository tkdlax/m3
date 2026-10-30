# Operations

## Modes

| Mode | Orders |
|---|---|
| `research_only` | Never |
| `paper_dry_run` (default) | Persist proposals / READY only; never submit |
| `paper_auto` | Paper submit after gates + resolved §21 limits + `auto_paper_orders_enabled` |

## Kill switch / emergency stop

Set `TRADING_PAUSED=true` (Compose default). Blocks new order submission and trading-day recovery submits; **research, reconciliation, reporting, and alerts continue**.

```bash
dividend-agent ops emergency-stop
docker compose stop agent          # hard stop
```

## Promote to paper_auto (operator)

No source-code edits required — only private config + env:

1. Run dry-run cycles until comfortable (`scheduler run --jobs startup,trading_day,monthly`).
2. In private `strategy.yaml`, set every null §21 field (see below) and:
   - `mode: paper_auto`
   - `execution.auto_paper_orders_enabled: true`
3. `dividend-agent config validate /config/strategy.yaml --check-auto-submit`
4. `./scripts/promote_paper_auto.sh /config/strategy.yaml /data/agent.sqlite3`  
   (or `ops record-promote` once the config already passes auto-submit checks)
5. Set `TRADING_PAUSED=false` in `.env` and recreate/restart the agent.
6. `broker verify-paper` then monitor `scheduler status` / `/reports`.

**§21 numeric fields that must be non-null before auto-submit:**  
`max_position_pct`, `max_portfolio_order_notional_usd`, `min_cash_reserve_usd`, `min_option_bid_usd_per_share`, `max_option_bid_ask_spread_pct`, `min_option_open_interest`, `max_new_stock_order_notional_usd`.

Additional policy decisions in PROJECT_SPEC §21 (premium floors, ex-div spanning, AI provider, etc.) should be written into STRATEGY/DECISION_POLICY before relying on unattended submits.

## Scheduler (M5)

Idempotent jobs under a single SQLite worker lock. Period keys prevent duplicate completed runs (use `--force` to override).

| Job | Cadence | Behavior |
|---|---|---|
| `startup` | Each boot / daily | Verify paper, schema, secrets presence, clock, capabilities; reconcile; expire stale approvals |
| `trading_day` | Exchange trading days | Reconcile, open orders, assignment/expiry + dividend alerts, drift; skip holidays; `skipped_paused` when kill switch on |
| `monthly` | Calendar month | Research refresh hook, 100-share lot scan, optional chain evaluate, partial accumulation notes, monthly JSON report |
| `event_review` | On demand | Focused review for fills / corp actions / outages / mismatches |

```bash
dividend-agent scheduler run --jobs startup,trading_day \
  --config /config/strategy.yaml --db /data/agent.sqlite3 --reports /reports
dividend-agent scheduler status --db /data/agent.sqlite3
```

## Common commands

```bash
dividend-agent config validate /config/strategy.yaml
dividend-agent config validate /config/strategy.yaml --check-auto-submit
dividend-agent broker verify-paper
dividend-agent broker reconcile --db /data/agent.sqlite3
dividend-agent ledger migrate --db /data/agent.sqlite3
dividend-agent scheduler run --jobs startup,trading_day --db /data/agent.sqlite3 --reports /reports
dividend-agent approvals list --db /data/agent.sqlite3
dividend-agent ops record-promote --config /config/strategy.yaml --db /data/agent.sqlite3
dividend-agent ops emergency-stop
dividend-agent health
```

## Backup / restore / upgrade / rollback

See [TRUENAS_DEPLOYMENT.md](TRUENAS_DEPLOYMENT.md). Helpers:

```bash
./scripts/backup.sh /data ./backup /reports /logs
./scripts/restore.sh ./backup/agent-data-….tar.gz /data
```

## Paper execution notes (M4)

- Client order IDs are stable (`da-<intent_uuid_hex>`). After timeout, **query by client order ID** before retry.
- Hard blocks are never approvable. Live Alpaca URLs are rejected.
- **Do not place live credentials on the host for v1.**
