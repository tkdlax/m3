# Release notes — M0–M6 paper stack

Stacked PRs (merge bottom-up):

| PR | Branch | Milestone |
|---|---|---|
| #1 | `cursor/m0-repository-skeleton-bafe` | M0 skeleton |
| #2 | `cursor/m1-broker-ledger-bafe` | M1 broker + ledger |
| #3 | `cursor/m2-research-bafe` | M2 research |
| #4 | `cursor/m3-options-engine-bafe` | M3 options engine |
| #5 | `cursor/m4-paper-execution-bafe` | M4 paper execution |
| #6 | `cursor/m5-autonomous-scheduler-bafe` | M5 scheduler |
| #7 | `cursor/m6-truenas-release-bafe` | M6 TrueNAS release |

## What an operator gets

From a fresh clone on TrueNAS SCALE Compose: configure paper credentials, run research + options dry-run, inspect cited recommendations, set explicit limits, then enable an autonomous **paper-only** cycle with audit logs and reconciliation. Backup/restore/upgrade/rollback and emergency stop are documented without source-code edits.

## Jake — §21 decisions before `paper_auto`

Until these are explicit in private `strategy.yaml` / ops notes, keep `paper_dry_run` + `TRADING_PAUSED=true`:

1. **Liquidity / premium:** min net call premium, max bid/ask spread %, min open interest, quote freshness (maps to null execution fields + strategy knobs).
2. **Sizing:** max issuer allocation (`max_position_pct`), max order notionals, min cash reserve, diversification rules.
3. **Stock buys:** fully automatic vs approval-gated; issuer qualification thresholds.
4. **Thesis breaks:** dividend cut / delayed filings / deterioration → sell, hold, or halt calls.
5. **Ex-dividend:** always skip spanning calls (current default `avoid_overlap`) vs approval-based spanning.
6. **Lots / tax:** cost basis per 100-share block; assignment lot selection.
7. **Orders:** limit policy, unfilled handling, trading hours.
8. **Data vendors:** market-data + dividend calendar providers and rate limits.
9. **AI research:** provider, token budgets, human review of memos (optional).
10. **Alerts:** CLI-only vs email/secure UI; destinations and auth.
11. **Paper trial success:** observation period and exit criteria before any future live discussion.

Numeric auto-submit blockers that must be non-null in config:  
`max_position_pct`, `max_portfolio_order_notional_usd`, `min_cash_reserve_usd`, `min_option_bid_usd_per_share`, `max_option_bid_ask_spread_pct`, `min_option_open_interest`, `max_new_stock_order_notional_usd`.

**Still out of v1:** live trading, unauthenticated approval UI, TrueNAS CORE Docker assumptions.
