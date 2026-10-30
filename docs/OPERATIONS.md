# Operations

## Modes

| Mode | Orders |
|---|---|
| `research_only` | Never |
| `paper_dry_run` (default) | Persist proposals only |
| `paper_auto` | Paper submit after gates + resolved limits |

## Common commands

```bash
dividend-agent config validate /config/strategy.yaml
dividend-agent broker verify-paper
dividend-agent broker reconcile --db /data/agent.sqlite3
dividend-agent broker status --db /data/agent.sqlite3
dividend-agent ledger migrate --db /data/agent.sqlite3
dividend-agent health
```

`broker reconcile` is read-only against Alpaca paper (no order submission). Without paper credentials it fails closed.

## Kill switch

Set `TRADING_PAUSED=true` (Compose default). Stops new orders when execution exists; reconciliation/reporting remain allowed.

## Cadence (M5)

Startup verify → trading-day drift checks → monthly research/call cycle → event-driven reviews → monthly report.

## Backups

See `scripts/backup.sh` (placeholder) and TrueNAS volume guidance.
