# Decision policy

Machine-oriented rules and open operator decisions. **Do not invent permissive defaults** for unresolved items.

## Hard blocks (never approvable in v1)

Live destination; uncovered short call; strike below cost floor; unverified adjusted deliverable; insufficient buying power; stale/missing data; contradictory state; unsupported order type; missing audit; duplicate order; invalid options permission; broker/exchange restriction.

### M3 covered-call gates (deterministic)

Implemented in `options.gates` / `strategy.candidate_selection` (evaluate only — **no submission**):

| Gate | Fail-closed behavior |
|---|---|
| Contract spec | Reject adjusted / non-100 deliverable |
| Coverage | Reject if unencumbered shares < 100×contracts (open calls + pending sells reserve shares) |
| Strike floor | `strike >= WAC` with Decimal compare (equality passes; one cent below fails) |
| OTM at entry | Reject if strike ≤ spot when `require_out_of_money_at_entry` |
| DTE | Must be within configured min/max |
| Quote freshness | Stale/missing quote timestamp rejects |
| Premium | Missing bid rejects |
| Liquidity (§21) | `min_option_bid`, `max_spread`, `min_oi` **null ⇒ reject** (no invented defaults) |
| Ex-dividend | `avoid_overlap`: missing calendar or overlap ⇒ reject |
| Chance of Profit | Default floor 0.90; 0.80–0.90 band is **review** unless `aggressive_mode_enabled` |

## Approval-required exceptions

Aggressive probability regime; ex-dividend-spanning call; unusual research; borderline issuer; premium/liquidity deviations; corporate actions; threshold changes; large position; ambiguous cost basis. Approvals never bypass hard blocks; must be action-specific, expiring, and revalidated.

## Config schema (v1)

See `config/strategy.example.yaml` and `dividend_agent.config.AppConfig`. Modes: `research_only` | `paper_dry_run` | `paper_auto`.

## Unresolved (§21) — open until operator sets

Until resolved, the system may research, screen, recommend, simulate, and report, but **must not auto-submit** trades that depend on these:

1. Min worthwhile **net** call premium; acceptable bid/ask spread, volume, OI, quote freshness.
2. Max issuer allocation, max order notional, total deployed capital, min cash reserve, diversification.
3. Whether stock purchases may be fully automatic; exact issuer qualification thresholds.
4. Actions on dividend cuts, delayed filings, or thesis deterioration.
5. Whether approved ex-dividend-spanning calls are allowed, or always skipped.
6. Cost-basis definition per 100-share block; assignment tax-lot tracking.
7. Order types, limit price policy, unfilled-order handling, trading hours.
8. Market-data and dividend-calendar providers, coverage, rate limits.
9. AI provider (if any), token budgets, memo review requirements.
10. Approval/alert delivery, authentication, destinations.
11. Definition of a successful paper trial and observation period before any future live consideration.

Mapped null YAML keys today: `execution.max_position_pct`, `max_portfolio_order_notional_usd`, `min_cash_reserve_usd`, `min_option_bid_usd_per_share`, `max_option_bid_ask_spread_pct`, `min_option_open_interest`, `max_new_stock_order_notional_usd`.
