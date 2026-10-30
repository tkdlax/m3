# Dividend Income + Covered Call Agent

Paper-only autonomous agent that researches established U.S. dividend equities, accumulates shares, and proposes **fully covered** calls inside an **Alpaca paper** account.

> **Not production-ready.** Paper-only vertical slice through M1 (broker read + SQLite ledger). There is **no live trading path**, no live credentials, and no live-trading toggle in v1. M1 does **not** submit orders.

## Status

| Milestone | State |
|---|---|
| M0 Repository skeleton | Done |
| M1 Broker + ledger | Done (prior PR) |
| M2 Research | Done (prior PR) |
| M3 Options engine | Done (prior PR) |
| M4 Paper execution | Done (prior PR) |
| M5 Autonomous scheduler | In progress (this tree) |
| M6 | See `docs/ARCHITECTURE.md` |

Default operating mode: **`paper_dry_run`** — full decision pipeline later; never submits orders until explicitly promoted after guardrails are set.

## Quickstart

Requirements: Python **3.12+**, [uv](https://docs.astral.sh/uv/), Docker (optional).

```bash
git clone https://github.com/tkdlax/m3.git
cd m3
./scripts/bootstrap.sh
make check
uv run dividend-agent config validate config/strategy.example.yaml
uv run dividend-agent health
uv run dividend-agent broker verify-paper   # fails closed without paper creds
# With ALPACA_PAPER_* set against paper-api.alpaca.markets:
# uv run dividend-agent broker reconcile --db ./data/agent.sqlite3
# uv run dividend-agent broker status --db ./data/agent.sqlite3
```

### Docker Compose (dry-run)

```bash
cp .env.example .env
# edit SEC_USER_AGENT; set Alpaca *paper* keys only when ready to reconcile
docker compose up -d --build
docker compose exec agent dividend-agent health
docker compose exec agent dividend-agent config validate /config/strategy.yaml
```

TrueNAS dataset mounts: see [docs/TRUENAS_DEPLOYMENT.md](docs/TRUENAS_DEPLOYMENT.md) and `compose.truenas.yaml`.

## Safety model

- **AI researches; deterministic code authorizes.**
- **Paper-only.** Unverified paper → fail closed (no orders).
- **Null §21 guardrails** (`max_position_pct`, cash reserve, option liquidity floors, etc.) block auto-submit until the operator sets them. Research/dry-run may still run.
- Kill switch env: `TRADING_PAUSED=true` (default in Compose).

## CLI

| Command | Behavior |
|---|---|
| `dividend-agent version` | Print version |
| `dividend-agent health` | Local health JSON |
| `dividend-agent config validate PATH` | Schema + paper-only validation |
| `dividend-agent config validate PATH --check-auto-submit` | Also fail if auto-submit blockers remain |
| `dividend-agent broker verify-paper` | Attest paper account; **fails closed** without verified paper |
| `dividend-agent broker reconcile [--db PATH]` | Sync account/positions/orders/activities → SQLite (no trades) |
| `dividend-agent broker status [--db PATH]` | Show last ledger snapshot |
| `dividend-agent ledger migrate [--db PATH]` | Apply SQLite migrations |
| `dividend-agent research symbol TICKER` | SEC-backed cited issuer memo (accept/reject/review) |
| `dividend-agent research screen TICKER` | Deterministic yield/52-week/liquidity screen |
| `dividend-agent options evaluate CHAIN.json` | Covered-call gates → TradeIntents |
| `dividend-agent trade status\|recover` | Local order lifecycle / restart recovery |
| `dividend-agent approvals list\|approve\|reject` | Exception approval queue |
| `dividend-agent scheduler run\|status` | Idempotent startup / trading-day / monthly / events |

## Layout

Package lives under `src/dividend_agent/` (see product layout in the project spec). Config examples: `config/strategy.example.yaml`. Docs: `docs/`.

## Documentation

- [STRATEGY](docs/STRATEGY.md) · [ARCHITECTURE](docs/ARCHITECTURE.md) · [DECISION_POLICY](docs/DECISION_POLICY.md)
- [RISK_AND_LIMITATIONS](docs/RISK_AND_LIMITATIONS.md) · [ALPACA_INTEGRATION](docs/ALPACA_INTEGRATION.md)
- [RESEARCH_SOURCES](docs/RESEARCH_SOURCES.md) · [PROBABILITY_MODEL](docs/PROBABILITY_MODEL.md)
- [OPERATIONS](docs/OPERATIONS.md) · [TRUENAS_DEPLOYMENT](docs/TRUENAS_DEPLOYMENT.md) · [SECURITY](docs/SECURITY.md)
- Portable agent instructions: [SKILL.md](SKILL.md) · coding agents: [AGENTS.md](AGENTS.md)

## License

MIT — see [LICENSE](LICENSE).
