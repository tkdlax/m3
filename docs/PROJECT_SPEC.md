# Dividend Income + Covered Call Autonomous Agent

**Master product requirements, investment philosophy, architecture, and implementation specification**  
**Status:** Build specification, v1.0  
**Target:** Portable Python application; Alpaca paper trading; Docker Compose on TrueNAS; GitHub repository as source of truth.  
**Authority:** This document describes the user's intended strategy, not a claim that the strategy produces guaranteed returns or is suitable for any particular account.

## 1. Mission and non-negotiable design principles

Build an auditable, modular system that researches established dividend-paying U.S. equities, accumulates shares, and sells **fully covered** calls on eligible 100-share lots. It should operate independently within explicit constraints in an **Alpaca paper account**, requesting human approval for ambiguous decisions and defined exceptions. It must be portable across AI agents and brokerage integrations.

1. **AI researches; deterministic code authorizes.** An LLM can analyze filings, propose candidates, and explain decisions; it cannot override executable safeguards.
2. **Paper-only v1.** Fail closed if an endpoint, credential, or account cannot be positively verified as paper trading. No live order route, credentials, or live-trading toggle in the initial release.
3. **Capital preservation is a strategy preference, not a guarantee.** Never propose a covered-call strike below the weighted-average acquisition price of the relevant shares; do not imply this prevents market losses or all tax-lot losses.
4. **No naked calls.** Reconcile underlying shares, existing options, pending orders, and corporate-action-adjusted deliverables before authorizing an order.
5. **No forced monthly trading.** A missing qualifying opportunity is a valid outcome. Hold shares and collect eligible dividends rather than break a rule.
6. **No invented market data or probabilities.** Use fresh, timestamped brokerage data and documented model assumptions. If data are missing or stale, do not trade.
7. **Traceable decisions.** Every candidate, rejection, recommendation, approval, order, fill, reconciliation, and model output must have a timestamped record and relevant source references.
8. **Least privilege.** Restrict trading capability to a single execution service; research agents have read-only access and cannot directly call order-submission tools.
9. **Reproducibility and portability.** Strategy configuration, model versions, adapters, and deployment manifests belong in Git. Secrets, brokerage records, and private reports do not.

## 2. Strategy philosophy (as communicated)

### 2.1 Stock selection

Seek established, financially stable dividend-paying companies with indicative annual dividend yields **around 5%–7%**. A materially higher yield (e.g., 9%–10%) is a warning to investigate distress or dividend sustainability, not an automatic sign of safety. Lower yields may be unattractive for the income target. Illustrative historical names mentioned: General Mills (GIS), AT&T (T), and Pfizer (PFE); **none are preapproved**, and current yields must be checked.

Review the stock's position in its 52-week price range to avoid blindly buying near a recent high; do not treat the 52-week low as proof of undervaluation or try to time the exact bottom. Read recent 10-K, subsequent 10-Q, relevant 8-K, earnings information, cash flow, leverage, debt maturities, payout coverage, business durability, and material risks. The goal is a company plausibly viable for years, not a short-term trade.

### 2.2 Share accumulation

Prefer buying at least **100 shares** per stock to support one standard U.S. equity covered-call contract. Smaller positions are permitted when capital is insufficient; track them and accumulate until enough shares exist. Verify actual option deliverables, since adjusted contracts can differ from the usual 100-share standard.

### 2.3 Covered-call income

Sell covered calls with a target expiration of approximately **30 calendar days**, one contract per 100 unencumbered shares for standard contracts. Select a strike **at or above weighted-average purchase cost per share** and normally above the current market price. Prefer Robinhood-style **Chance of Profit (short call)** >=90%; 80%–<90% is an explicitly more aggressive regime, **not automatically enabled** in v1. Evaluate whether the available premium is worth collecting. A distant strike may have a negligible or unmarketable bid; no trade is required.

The intended outcome is to collect the premium, keep the shares, remain eligible for dividends, and repeat approximately monthly. Normally hold calls to expiration, but provide an exception workflow for early assignment risk, erroneous orders, and corporate actions.

**Important probability distinction:** Chance of Profit for the short call is not the probability of retaining shares, nor the probability of a profitable combined stock-and-call position. Separately report probability of expiring out of the money, probability of touching (if available), assignment risk indicators, and total-position break-even. Exact Robinhood parity is not assumed. Document the implemented pricing model and compare it with known references before relying on it.

### 2.4 Expiration, assignment, and reinvestment

If a call expires unexercised, keep shares and premium, reassess and optionally write a new call. If assigned, reconcile the share sale at the contractual deliverable and strike, retain premium, compute realized gains/losses, and search for a new eligible dividend stock. If proceeds cannot fund 100 shares, buy a smaller position when appropriate and accumulate; do not force a lower-quality stock simply to complete a lot.

### 2.5 Declines and cost basis

When price falls below average acquisition cost, **do not write a call with a strike below that cost**. Either write an above-cost call with a sufficient premium or pause call writing and continue holding if the business remains qualified. A decline remains an **unrealized economic loss**, even when shares are not sold. Weighted-average cost is a strategy threshold; brokerage tax lots and realized tax results must be tracked separately. Multiple 100-share blocks, lots, and assigned share selection need explicit reconciliation.

### 2.6 Dividend dates

Review ex-dividend dates and declared dividends before writing or maintaining calls. American-style calls may be assigned before expiration; early exercise is especially relevant when a call is in the money and remaining extrinsic value is small relative to the dividend. The preferred default is to avoid opening calls that span a known ex-dividend date **unless an explicit, tested policy permits it**. A higher modeled Chance of Profit does not guarantee dividend retention. Dividend entitlement follows actual ownership and the ex-dividend rules, not merely having sold a call.

### 2.7 Return reporting

Report separately (a) cash dividends, (b) option premiums **net of closing costs and fees**, (c) realized share gains/losses, and (d) unrealized share appreciation/depreciation. The strategy's illustrative income aspiration is 5%–7% dividend yield plus 2%–3% annual option premium yield, but **7%–10% income is not a guaranteed floor or total portfolio return**. Use consistent denominators and time periods. Show time-weighted return and/or money-weighted return where feasible, cash flows, total equity, assignment proceeds, and taxes/fees where known. Do not count the same premium twice when a position closes.

## 3. Scope and delivery stages

### MVP (required)

- Dockerized, unattended paper-account operation with a scheduler, health checks, persistent state, and restart recovery.
- Alpaca adapter for paper account identification, balances, positions, orders, assets, quotes, option chains, options orders, and account activities, with capability discovery and fail-closed behavior.
- SEC EDGAR filing retrieval, structured financial facts, filing citations, caching, and evidence-backed company screening.
- Deterministic screening and risk gates; optional LLM-written research memos with citations and confidence/uncertainty flags.
- Covered-call candidate generation, model-based probability calculation, strike-floor and coverage checks, dividend calendar check, liquidity checks, and order pricing.
- Autonomous execution of **preapproved categories of paper orders** that pass every hard gate; all other situations queue for approval.
- Order state machine, idempotency, trade journal, reconciliation, reporting, and alerting.
- Full automated test suite, replayable scenarios, and sample paper-mode demo.

### Later, not v1

- Live brokerage trading; additional brokerages; multi-account trading; options spreads; rolling automation; portfolio tax optimization; sophisticated dashboards; automatic strategy tuning; high-frequency activity. These require separate design and explicit authorization.

## 4. Operating modes and authority

| Mode | Behavior |
|---|---|
| `research_only` | Gather data, produce reports; never submit orders |
| `paper_dry_run` | Run full decision pipeline, persist proposed orders; never submit |
| `paper_auto` | Submit eligible orders to verified Alpaca paper account only; exceptions queue for human approval |

Default on first deployment: **`paper_dry_run`**. Promotion to `paper_auto` requires an explicit operator setting after successful smoke tests. All modes must reject live endpoints and live account credentials.

**Hard blocks (never approvable in v1):** live destination; short call not demonstrably covered; strike below defined cost floor; unverified adjusted deliverable; insufficient buying power; stale/unavailable market data; contradictory positions/orders; unsupported option or order type; missing audit trail; duplicate order; invalid account/options permission; order violating exchange/broker restrictions.

**Approval-required exceptions (do not trade until approved):** aggressive 80%–<90% probability regime; an ex-dividend-spanning call; unusual research findings; a borderline issuer; deviation from minimum premium/liquidity policy; newly detected corporate action; changed strategy thresholds; unexpectedly large position; any ambiguous cost basis or ownership state. Approval never bypasses hard blocks. An approval must be specific to a proposed action, have an expiry, and be invalidated by material price/position changes.

## 5. Decision pipeline

1. **Reconcile first:** verify paper account, cash, buying power, stock holdings, options, pending orders, fills, dividends, corporate actions, and last-known internal state.
2. **Discover:** screen optionable U.S. stocks for dividend yield, share affordability, market liquidity, and 52-week position.
3. **Research:** retrieve latest annual 10-K, later 10-Q and material 8-Ks, financial facts, dividend history and announcements; produce evidence-backed issuer memo.
4. **Qualify:** run configurable, explicit financial rules and reject missing or unverified fundamentals; LLM can propose interpretation but not invent a pass.
5. **Allocate:** apply cash reserve, issuer concentration, maximum new exposure, and share-count rules; hold or accumulate partial positions as appropriate.
6. **Select calls:** for every unencumbered eligible lot, inspect option chain and actual bids/asks, expiry, strike, liquidity, deliverable, implied volatility, dividend calendar, and modeled probabilities.
7. **Validate:** enforce all hard gates with fresh snapshots immediately before placing an order.
8. **Execute:** submit paper order with idempotent client order ID, log response, track fills/partial fills/cancellations, and avoid accidental resubmission after timeouts.
9. **Monitor:** review expiring contracts, early-assignment indicators, ex-dividend dates, issuer events, open orders, and account drift; do not assume assignment solely from price.
10. **Reconcile and report:** confirm authoritative broker events and holdings, compute cash/economic returns, produce daily exception digest and monthly strategy report.

## 6. Proposed repository layout

```text
dividend-covered-call-agent/
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── SECURITY.md
├── CHANGELOG.md
├── .gitignore
├── .env.example
├── pyproject.toml
├── uv.lock                         # or pinned requirements/lockfile
├── Dockerfile
├── compose.yaml
├── compose.truenas.yaml             # optional documented override
├── Makefile
├── SKILL.md                         # portable agent instructions
├── AGENTS.md                        # coding-agent repo guidance
├── config/
│   ├── strategy.example.yaml
│   └── logging.example.yaml
├── docs/
│   ├── STRATEGY.md                  # investor philosophy and rules
│   ├── ARCHITECTURE.md
│   ├── DECISION_POLICY.md
│   ├── RISK_AND_LIMITATIONS.md
│   ├── ALPACA_INTEGRATION.md
│   ├── RESEARCH_SOURCES.md
│   ├── PROBABILITY_MODEL.md
│   ├── OPERATIONS.md
│   ├── TRUENAS_DEPLOYMENT.md
│   ├── SECURITY.md
│   └── ADR/                         # architecture decisions
├── src/dividend_agent/
│   ├── cli.py
│   ├── app.py
│   ├── config.py
│   ├── domain/                       # typed domain models and events
│   ├── brokers/
│   │   ├── interface.py
│   │   ├── alpaca_mcp.py
│   │   └── alpaca_paper.py            # optional SDK/REST fallback
│   ├── research/
│   │   ├── sec_edgar.py
│   │   ├── company_facts.py
│   │   ├── dividends.py
│   │   ├── screener.py
│   │   └── memo.py
│   ├── options/
│   │   ├── chains.py
│   │   ├── probability.py
│   │   ├── contract_spec.py
│   │   └── ex_dividend.py
│   ├── strategy/
│   │   ├── qualification.py
│   │   ├── allocation.py
│   │   ├── candidate_selection.py
│   │   └── policy.py
│   ├── execution/
│   │   ├── validator.py
│   │   ├── approval_queue.py
│   │   ├── order_manager.py
│   │   └── reconciliation.py
│   ├── portfolio/
│   │   ├── lots.py
│   │   ├── positions.py
│   │   ├── activities.py
│   │   └── performance.py
│   ├── agents/
│   │   ├── interface.py
│   │   └── research_agent.py
│   ├── storage/
│   │   ├── models.py
│   │   ├── migrations/
│   │   └── repository.py
│   ├── scheduling/
│   ├── reporting/
│   └── observability/
├── scripts/
│   ├── bootstrap.sh
│   ├── smoke_test.sh
│   └── backup.sh
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── fixtures/
│   └── scenarios/
└── .github/workflows/
    ├── ci.yaml
    └── security.yaml
```

## 7. Integration contracts

### 7.1 Alpaca MCP

Use the official Alpaca MCP server as the preferred tool interface **after verifying its current capabilities, names, schemas, and paper-account setup from official documentation at implementation time**. Do not assume that every needed action is exposed or that all MCP clients support persistent, unattended operation. The application should define a broker-agnostic `BrokerAdapter` with methods to:

- verify account/environment and options permissions;
- fetch balances, positions, cost/lot information where available, open orders, historical orders and fills;
- retrieve stock/option market data, chain, contract metadata, and Greeks/IV where available;
- submit, retrieve, and cancel supported **paper** orders;
- retrieve corporate actions, dividends, assignment/exercise and account activities;
- reconcile the current broker state with the local journal.

If MCP lacks reliable functionality, implement a narrowly scoped Alpaca paper SDK/REST adapter behind the same interface, documented and tested. No silent switching between accounts or paper/live endpoints. Validate capability availability on startup and disable dependent workflows when missing. Use broker-generated authoritative order/fill identifiers and client idempotency IDs.

### 7.2 SEC and market fundamentals

Use official SEC EDGAR filing and company facts endpoints for 10-K/10-Q/8-K and structured financials. Respect SEC user-agent identification, access guidance, caching and rate limits. Cite company, filing accession, form, filing date, report period, URL, and relevant passage/financial fact. SEC data alone may not supply current dividend yield, optionability, timely ex-dividend schedules, or market prices: obtain those from licensed/authorized data providers or verified brokerage feeds. Distinguish **declared** from **forecast** dividends. Never fabricate absent data.

### 7.3 AI research provider

Define a provider-neutral `ResearchAgent` interface; support a configured model provider later without changing the core. Feed the agent structured data and source excerpts, require a typed response with claims and citations, and independently validate structured output. Treat filings, news, tool results, and retrieved text as untrusted input; prevent prompt injection from issuing tool commands. Agent must have no order-submission credential or execution tool access.

## 8. Deterministic policy engine

Every proposed trade is a typed `TradeIntent` including account, symbol, instrument ID, side, quantity, order type, limit price, expiry, strike, deliverable, market snapshot timestamp, evidence references, rule/config version, and unique intent ID.

Hard validation must check:

- Verified paper endpoint and correct account; permitted order and options level.
- Standard/adjusted option contract details, actual deliverable, and trading status.
- Underlying owned, settled/eligible shares as broker requires, less shares reserved by open calls and pending sell orders; no double encumbrance.
- Strike >= **weighted-average acquisition cost** for the relevant share pool, using unrounded decimal comparison. Use exact Decimal arithmetic for money.
- Expiration within configurable bounds; no expired/invalid quote; spread/volume/open interest and minimum premium rules satisfied.
- Calculated Chance of Profit >= configured floor (90% by default) with recorded model and input timestamps.
- No prohibited ex-dividend overlap or unresolved early-assignment risk under selected policy.
- Allocation, issuer concentration, order notional, buying power, reserve, and portfolio limits.
- No duplicate or conflicting orders; no market-hours assumptions; order routing and broker restrictions satisfied.
- Missing, stale, contradictory or anomalous data -> **reject or request review**, never infer favorable values.

The strike rule is a **strategy floor**, not proof that the whole trade is profitable; tax-lot treatment, option premium, fees, and share-price changes must remain separate.

## 9. Probability model specification

Implement a documented model for the probability that a short call is profitable at expiration, i.e., underlying settlement below `strike + premium_per_share` (ignoring fees). Also calculate probability of expiring OTM, which is the more direct expiration proxy for retaining shares. American-style calls on dividend stocks can be exercised early; these model probabilities **do not** calculate exact assignment probability.

Use observed implied volatility where possible, remaining time to expiration, rates, and expected dividends or an appropriate dividend-adjusted model. Black-Scholes-style terminal probabilities may be an initial approximation; document risk-neutral vs real-world probability, dividend assumptions, volatility skew, and model limitations. A model with American exercise (e.g., Bjerksund-Stensland) may help price options but does not automatically yield a calibrated early-assignment probability. Never claim exact parity with Robinhood without independent verification. Test against known analytic cases and representative broker-displayed values. Store premium as **per-share** and **per-contract** explicitly.

Use quote-aware premium estimates (bid or conservative executable limit), not just last trade or midpoint. Show net premium after known fees and compare to required thresholds. Do not extrapolate monthly premiums into guaranteed annual yields.

## 10. Research and qualification framework

Each stock research memo should include:

- Identity: ticker, CIK, sector, business summary, listing and optionability.
- Current price, quote timestamp, 52-week high/low and position in range.
- Dividend: most recent declared amount, frequency, annualized/forward/trailing yield (clearly labeled), ex-dividend/payment dates, payment history and cuts.
- Financials: revenue/earnings trends, operating cash flow, free cash flow (definition stated), debt/net debt, interest coverage, maturities, liquidity, dividend coverage, dilution and notable one-time items.
- Business risks: concentration, regulation, competitive position, litigation, material 10-K risk factors, latest 10-Q and relevant 8-K changes.
- Data freshness, source links, citations, unknowns, pass/fail per criterion, and human-readable investment thesis.

No LLM-generated binary 'safe from bankruptcy' assertion. Company stability is an assessed, uncertain judgment. Use configurable quantitative gates and flag ambiguous cases for review. Never treat the illustrative companies as automatically eligible.

## 11. Strategy configuration example

```yaml
schema_version: 1
mode: paper_dry_run
broker:
  provider: alpaca
  environment: paper
  require_positive_paper_verification: true
strategy:
  dividend_yield_target_min: 0.05
  dividend_yield_target_max: 0.07
  unusually_high_yield_review_at: 0.09
  require_latest_10k: true
  require_subsequent_10q_review: true
  inspect_52_week_range: true
  allow_partial_share_positions: true
  covered_calls:
    target_days_to_expiry: 30
    min_days_to_expiry: 21
    max_days_to_expiry: 45
    chance_of_profit_min: 0.90
    aggressive_mode_enabled: false
    aggressive_probability_floor: 0.80
    strike_floor: weighted_average_acquisition_cost
    require_out_of_money_at_entry: true
    contracts_per_standard_lot: 1
    skip_if_premium_not_worthwhile: true
    ex_dividend_policy: avoid_overlap
    early_assignment_review_enabled: true
execution:
  auto_paper_orders_enabled: false
  human_approval_on_exceptions: true
  hard_blocks_cannot_be_overridden: true
  require_fresh_quote_seconds: 60
  require_idempotency_key: true
  # These MUST be decided before auto trading; null means fail closed.
  max_position_pct: null
  max_portfolio_order_notional_usd: null
  min_cash_reserve_usd: null
  min_option_bid_usd_per_share: null
  max_option_bid_ask_spread_pct: null
  min_option_open_interest: null
  max_new_stock_order_notional_usd: null
research:
  sec_user_agent: ${SEC_USER_AGENT}
  max_age_days_for_research_memo: 30
notifications:
  channel: file
storage:
  sqlite_path: /data/agent.sqlite3
```

**These values are examples, not hidden decisions.** `null` guardrails are deployment blockers for autonomous order submission. The implementer must expose them to the operator and record the chosen values before `paper_auto` is enabled. Dividend yield is a target screening range, not an inflexible guarantee of issuer quality. A stock below the cost floor may be held without calls.

## 12. Approval queue and exception lifecycle

Store approvals as durable records with type, proposed action, exact account/instrument/order, reason, risk details, relevant data snapshot, expiration time, and status (`pending`, `approved`, `rejected`, `expired`, `superseded`). Provide CLI and optionally a small local authenticated web interface. Never approve by free-text chat interpretation alone. On approval, **revalidate every hard gate and material market input** before submission. Approvals cannot authorize live trading or uncovered calls. Send notifications for approvals required, orders filled, unexpected assignment, reconciliation mismatch, research-data failure, and service degradation.

## 13. Order lifecycle and reconciliation

Implement an explicit state machine: `proposed -> validated -> awaiting_approval|ready -> submitted -> acknowledged -> partially_filled|filled|canceled|rejected|expired`, with error and reconciliation states. Record request and broker response, including timeout ambiguity. After network failure, **query by client order ID** before any retry. Reconcile positions and option obligations after fills, expirations, exercises, assignments, dividend credits, splits, and other corporate actions. Never infer shares were sold or retained merely from the underlying market price. Include restart recovery and a single active execution worker or distributed lock to prevent duplicate orders.

## 14. Persistence and auditability

Use SQLite with WAL mode for a single-instance MVP, migrations, and transaction boundaries; store database in a durable mounted dataset. Tables/entities should include accounts, holdings, lots, options positions, broker activities, orders, order events, market snapshots, issuers, filings, research memos, strategy decisions, policy versions, approvals, cash flows, and job runs. Preserve append-only audit events. Consider PostgreSQL only if concurrent writers or operational scale require it. Support backup, restore, and schema migration tests. Do not commit local DB, reports with private holdings, or API secrets.

## 15. Scheduler and operational cadence

- **On startup:** verify paper environment, schema, secrets, clock, broker capabilities; reconcile before any decision.
- **Trading days:** check open orders, assignment/expiry state, account drift, dividend events, and alert queue. Use exchange calendars and timezone-aware timestamps; handle holidays and DST.
- **Monthly:** perform research refresh, evaluate existing 100-share holdings, select qualifying calls, and evaluate reinvestment/partial-share accumulation.
- **On events:** unexpected fill, corporate action, material issuer filing, dividend cut, data outage, or detected broker mismatch triggers a focused review.
- **Monthly report:** dividends received, net option premiums, realized/unrealized stock P&L, total return, account equity, assignments, pending approvals, skipped opportunities and reasons, and research freshness.

The scheduler must be idempotent, restart-safe, and able to pause all trading independently of research/reporting.

## 16. Docker and TrueNAS deployment

### 16.1 Goals

Support a standard Docker Compose installation on a TrueNAS system **where Docker/Compose is supported by the installed TrueNAS edition/version** (e.g., current SCALE Apps/custom application facilities). Do not assume TrueNAS CORE provides the same Docker runtime. Document tested TrueNAS release(s) and the correct installation path. No privileged containers, host networking, Docker socket mounting, or Kubernetes dependency.

### 16.2 Compose services

Minimum: `agent` service with scheduler and CLI. Optional: `approval-ui` service bound to localhost/LAN behind authentication, and `db` only if PostgreSQL is chosen. A single service is preferable initially. Container runs as non-root with read-only root filesystem when practical, `no-new-privileges`, minimal Linux capabilities, resource limits, graceful shutdown, and health check. Persist `/data`, `/reports`, and `/logs` to TrueNAS datasets. Configuration mounted read-only; secrets supplied securely via env/secret mounts, never embedded in the image.

### 16.3 Compose design requirements

- `docker compose up -d` should start an initialized **dry-run** deployment after `.env` and config are supplied.
- Separate `.env.example` with placeholder values; never commit `.env`.
- Pin image dependencies and base image; publish multi-architecture images if feasible (amd64/arm64).
- Image builds locally and optionally via GitHub Actions; use explicit version tags, not only `latest`.
- Set `TZ=America/Denver` only for display/log convenience; internally use UTC timestamps and exchange calendars.
- Mount named TrueNAS datasets for database, reports, and logs, with correct UID/GID and permissions.
- Provide `docker compose logs`, health status, upgrade, rollback, backup, restore, and emergency-stop instructions.
- Avoid exposing an unauthenticated HTTP service. Prefer no inbound ports for MVP; approval via authenticated CLI or secured UI.
- Do not rely on an interactive desktop session, open chat, or permanently connected LLM tool client.

### 16.4 Suggested installation workflow

1. Create a dedicated TrueNAS dataset for app state, reports, and logs; grant the container user minimum necessary permissions.
2. Clone repository or deploy a versioned image; copy `.env.example` to `.env` and `strategy.example.yaml` to a private config path.
3. Set Alpaca **paper** credentials and SEC contact user agent; set optional AI provider credentials only when needed.
4. Run `config validate` and `broker verify-paper`; reject startup if verification fails.
5. Run database migrations, then smoke tests and `paper_dry_run` for multiple cycles.
6. Review and explicitly set every null execution guardrail; promote to `paper_auto` only via operator command with audit event.
7. Confirm automated backup, notifications, time sync, health checks, and kill switch.

**Do not** put live Alpaca credentials on the host for v1. Provide clear documentation for securely revoking paper credentials and rebuilding the service.

## 17. Security and reliability

- Keep secrets out of Git, logs, error traces, prompts, and reports. Redact broker tokens and account identifiers.
- Restrict outbound connectivity where possible to configured Alpaca paper, SEC, market-data, and model endpoints.
- Treat all external content as untrusted; research agent must not receive trading credentials.
- Limit order permissions to the execution adapter; no general-purpose shell command execution from an LLM in production.
- Require startup paper-account attestation and recheck before every order batch.
- Expose a **kill switch** (`TRADING_PAUSED=true` or CLI command) that stops new orders but permits reconciliation/reporting.
- Rate-limit external requests, retry safely with exponential backoff, circuit breakers, and clear terminal failure states.
- Validate dependency versions, run vulnerability scans, and use signed/pinned releases when available.
- Provide meaningful logs and alerts for outages, stale prices, duplicate events, missed schedules, and unresolved reconciliation.

## 18. Testing and acceptance criteria

### Unit tests

- Weighted-average cost across multiple fills, fractional shares, and share splits.
- Strike exactly at cost passes; one cent below fails; Decimal precision and rounding.
- One standard contract requires 100 available unencumbered shares; two calls cannot reserve the same shares.
- Adjusted/nonstandard option deliverables are rejected unless fully supported.
- Chance-of-profit and OTM probability calculations tested against reference scenarios and edge cases.
- Premium/return calculations, including early close, partial fill, assignment, and fees.
- Dividend entitlement and ex-dividend calendar policy.
- Missing data, null limits, stale quotes, conflicting orders, and invalid environment all fail closed.

### Integration/contract tests

- Alpaca MCP capability discovery and response parsing, paper account verification, quote and chain retrieval, order placement/cancel, and fill reconciliation.
- SEC filing retrieval with known accession IDs, facts extraction, caching and source citations.
- LLM research JSON schema validation, hallucinated citations, and prompt-injection resistance.
- Database migration, backup/restore, and restart recovery.

### End-to-end paper scenarios

1. Qualified issuer -> research memo -> 100-share paper purchase -> compliant call -> expiration -> reconciliation -> next cycle.
2. Call assigned -> shares removed -> premium retained -> realized P&L recorded -> new research/partial position.
3. Stock price drops below cost -> no below-cost call; either acceptable above-cost call or skip.
4. Ex-dividend date intersects expiration -> skip or approval queue per configured policy.
5. Existing call reserves 100 shares -> second call blocked.
6. Network timeout after submit -> query by client order ID; no duplicate.
7. Stock split/adjusted contract -> correct reconciliation or halt.
8. Paper endpoint unavailable or live endpoint presented -> **no order submitted**.
9. Restart mid-order -> recover from broker truth, not stale local assumptions.
10. Agent recommends violating a rule -> validator blocks and logs reason.

**Release acceptance:** All hard-gate tests pass; paper verification is enforced at the network and application boundary; dry-run reports match brokerage state; no duplicate orders after restart; a documented paper trade can be traced from cited research to order to fill and performance report; TrueNAS Compose installation, upgrade, backup and restore work from README instructions.

## 19. GitHub engineering standards

- `README.md`: mission, paper-only warning, quickstart, TrueNAS Docker instructions, architecture diagram, sample report, command reference, troubleshooting, links to docs.
- `SKILL.md`: concise portable agent role, permitted tools, workflow, required outputs, and hard prohibitions; reference canonical docs rather than duplicating every rule.
- `AGENTS.md`: repo navigation, development/testing commands, style rules, constraints, and PR checklist.
- `docs/STRATEGY.md`: faithful investor philosophy and edge cases; distinguish user-specified rules from implementation defaults.
- `docs/DECISION_POLICY.md`: machine-verifiable rule definitions, precedence, exception taxonomy, config schema.
- `docs/PROBABILITY_MODEL.md`: equations, assumptions, validation examples, model limitations.
- `docs/TRUENAS_DEPLOYMENT.md`: version-specific setup, datasets, UID/GID, secrets, compose, upgrades, backup/restore, logs.
- CI: lint/format/type checks, unit/integration tests with mocks, secret scanning, dependency/security checks, Docker build.
- PR template: what changed, tests run, policy changes, migrations, paper-trading impact, rollback plan.
- GitHub Issues/milestones: deliver vertical slices, not a single unreviewable giant PR.
- Protect default branch; review changes to execution, policy, credentials, and Compose files.

## 20. Implementation milestones and definition of done

**M0 — Repository skeleton:** pyproject, typed models, config validation, Compose, CI, README, SKILL.md, AGENTS.md. Done when a fresh clone builds and starts safely in dry-run mode.

**M1 — Broker and ledger:** Alpaca paper verification, positions/orders/activities, SQLite migrations, reconciliation. Done when broker state is read reliably and recorded without placing trades.

**M2 — Research:** SEC filings, structured facts, dividend data, stock screener, sourced issuer memo. Done when a candidate can be accepted/rejected with traceable evidence and missing-data behavior.

**M3 — Options decision engine:** chain, contract validation, probability, cost-floor and coverage checks, dividend policy. Done when all edge-case fixtures pass and proposed orders are explainable.

**M4 — Paper execution:** idempotent paper order placement, fill tracking, approval queue, audit events. Done when a paper order survives timeouts/restarts without duplication.

**M5 — Autonomous scheduler:** daily reconciliation, monthly strategy workflow, alerts, reports, kill switch. Done when unattended paper cycles operate under defined guardrails and exceptions pause safely.

**M6 — TrueNAS release:** Compose deployment tested on specified TrueNAS SCALE release, volume permissions, backup/restore, upgrade/rollback, operator runbook. Done when an operator can deploy from README without source-code edits.

## 21. Unresolved decisions (must be explicit, not guessed)

Before enabling autonomous paper orders, choose and document:

1. Minimum worthwhile **net** call premium and acceptable bid/ask spread, volume, open interest, quote freshness.
2. Maximum issuer allocation, maximum order notional, total deployed capital, minimum cash reserve, and diversification requirements.
3. Whether stock purchases may be fully automatic, and the exact issuer-qualification scoring/thresholds.
4. What to do when the issuer's dividend is cut, filings are delayed, or a thesis deteriorates.
5. Whether the strategy can write calls spanning ex-dividend dates after approval, or always skips them.
6. How to define cost basis for each 100-share block when different lots are held, and how to track assignment tax lots.
7. Order types, limit price policy, handling unfilled orders, and permissible trading hours.
8. Market-data and dividend-calendar providers, coverage, and rate limits.
9. Which AI provider (if any) to use, token budgets, and research memo review requirements.
10. Approval/alert delivery mechanism (CLI, email, secure local web UI), authentication, and notification destination.
11. Definition of a successful paper-trading trial and minimum observation period before considering any future live version.

Until these are resolved, the system may **research, screen, recommend, simulate, and report**, but must not auto-submit trades that depend on unspecified limits.

## 22. Instructions to the coding agent

Treat this file as the **product specification**, not as a suggestion to implement only a chat prompt. Create the repository, source code, tests, documentation, Dockerfile, Compose manifests, and runnable CLI. Work in milestone-sized commits/PRs with tests and setup instructions. Verify the current official Alpaca MCP and SEC interfaces before writing integration code. Prefer a minimal working vertical slice over mocked functionality presented as complete. Do not claim production readiness without successful paper integration tests. Do not introduce live-trading support in v1. If a requirement is ambiguous, create a tracked issue or a fail-closed configuration gate rather than inventing a permissive default.

**First demonstrable outcome:** From a fresh clone on a supported TrueNAS Docker environment, an operator can configure paper credentials, run a safe research-and-options dry run, inspect cited recommendations and rejection reasons, and—after setting explicit limits—enable an autonomous paper-only cycle with durable audit logs and verified broker reconciliation.
