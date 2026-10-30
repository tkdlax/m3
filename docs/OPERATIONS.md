# Operations

## Modes

| Mode | Orders |
|---|---|
| `research_only` | Never |
| `paper_dry_run` (default) | Persist proposals / READY only; never submit |
| `paper_auto` | Paper submit after gates + resolved §21 limits + `auto_paper_orders_enabled` |

## Kill switch

Set `TRADING_PAUSED=true` (Compose default). Blocks new order submission; reconciliation/reporting remain allowed.

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
dividend-agent health
```

## Paper execution (M4)

- Every submit path verifies paper, checks kill switch, and requires resolved null guardrails.
- Client order IDs are stable (`da-<intent_uuid_hex>`). After timeout, the manager **queries by client order ID** before any retry.
- Hard blocks (live destination, uncovered, strike below WAC, etc.) are never approvable.
- Exception approvals expire, can be superseded, and must revalidate before submit.
- `broker reconcile` remains read-only against Alpaca paper.

## Cadence (M5)

Startup verify → trading-day drift checks → monthly research/call cycle → event-driven reviews → monthly report.

## Backups

See `scripts/backup.sh` (placeholder) and TrueNAS volume guidance.
