# Dividend Income + Covered Call Agent

Paper-only autonomous agent that researches established U.S. dividend equities, accumulates shares, and proposes **fully covered** calls inside an **Alpaca paper** account.

> **Not production-ready.** M0 is a repository skeleton. There is **no live trading path**, no live credentials, and no live-trading toggle in v1.

## Status

| Milestone | State |
|---|---|
| M0 Repository skeleton | In progress (this tree) |
| M1 Broker + ledger | Not started |
| M2–M6 | See `docs/ARCHITECTURE.md` |

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
uv run dividend-agent broker verify-paper   # fails closed until M1 verifies paper
```

### Docker Compose (dry-run)

```bash
cp .env.example .env
# edit SEC_USER_AGENT; leave Alpaca keys empty for M0
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

## CLI (M0)

| Command | Behavior |
|---|---|
| `dividend-agent version` | Print version |
| `dividend-agent health` | Local health JSON |
| `dividend-agent config validate PATH` | Schema + paper-only validation |
| `dividend-agent config validate PATH --check-auto-submit` | Also fail if auto-submit blockers remain |
| `dividend-agent broker verify-paper` | Stub; **fails closed** without verified paper |

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
