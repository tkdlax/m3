# Dividend Income + Covered Call Agent

Paper-only autonomous agent that researches established U.S. dividend equities, accumulates shares, and writes **fully covered** calls inside an **Alpaca paper** account — deployable on TrueNAS SCALE via Docker Compose.

> **Not live trading.** There is **no live trading path**, no live credentials, and no live-trading toggle in v1. **Do not place live Alpaca API keys on the host.**

## Status

| Milestone | State |
|---|---|
| M0 Repository skeleton | Done |
| M1 Broker + ledger | Done |
| M2 Research | Done |
| M3 Options engine | Done |
| M4 Paper execution | Done |
| M5 Autonomous scheduler | Done |
| M6 TrueNAS release | In progress (this tree) |

Default mode: **`paper_dry_run`** with **`TRADING_PAUSED=true`**.

## Operator quickstart (no source edits)

### Local / laptop

```bash
git clone https://github.com/tkdlax/m3.git
cd m3
./scripts/bootstrap.sh
make check
uv run dividend-agent config validate config/strategy.example.yaml
uv run dividend-agent health
```

### Docker Compose (dry-run)

```bash
cp .env.example .env
# Set SEC_USER_AGENT; add ALPACA_PAPER_* only when ready (paper host only)
docker compose up -d --build
docker compose exec agent dividend-agent health
docker compose exec agent dividend-agent config validate /config/strategy.yaml
```

### TrueNAS SCALE

Follow **[docs/TRUENAS_DEPLOYMENT.md](docs/TRUENAS_DEPLOYMENT.md)** (datasets UID/GID **10001**, `compose.yaml` + `compose.truenas.yaml`).

### Promote path (paper_auto)

1. `config validate` → `broker verify-paper`
2. Dry-run scheduler cycles; inspect `/reports`
3. Set every null §21 limit in private `strategy.yaml`
4. Set `mode: paper_auto` and `execution.auto_paper_orders_enabled: true`
5. `config validate --check-auto-submit` → `./scripts/promote_paper_auto.sh …`
6. Set `TRADING_PAUSED=false` only when intentionally enabling paper submits

Details: [docs/OPERATIONS.md](docs/OPERATIONS.md). Remaining open policy choices: PROJECT_SPEC §21 / [docs/RELEASE_NOTES.md](docs/RELEASE_NOTES.md).

## Safety model

- **AI researches; deterministic code authorizes.**
- Unverified paper → fail closed (no orders).
- Null §21 guardrails block auto-submit.
- Kill switch: `TRADING_PAUSED` / `ops emergency-stop`.

## CLI

| Command | Behavior |
|---|---|
| `dividend-agent health` / `version` | Local health |
| `dividend-agent config validate PATH [--check-auto-submit]` | Schema + paper-only; optional auto-submit blockers |
| `dividend-agent broker verify-paper \| reconcile \| status` | Paper attestation + ledger sync |
| `dividend-agent research symbol\|screen` | SEC-backed research |
| `dividend-agent options evaluate` | Covered-call TradeIntents |
| `dividend-agent trade status\|recover` | Order lifecycle |
| `dividend-agent approvals list\|approve\|reject` | Exception queue |
| `dividend-agent scheduler run\|status` | Idempotent jobs |
| `dividend-agent ops record-promote \| emergency-stop` | Promote audit / kill-switch help |

## Documentation

- [TRUENAS_DEPLOYMENT](docs/TRUENAS_DEPLOYMENT.md) · [OPERATIONS](docs/OPERATIONS.md) · [RELEASE_NOTES](docs/RELEASE_NOTES.md)
- [STRATEGY](docs/STRATEGY.md) · [ARCHITECTURE](docs/ARCHITECTURE.md) · [ALPACA_INTEGRATION](docs/ALPACA_INTEGRATION.md)
- [SECURITY](docs/SECURITY.md) · [RISK_AND_LIMITATIONS](docs/RISK_AND_LIMITATIONS.md)
- [SKILL.md](SKILL.md) · [AGENTS.md](AGENTS.md)

## License

MIT — see [LICENSE](LICENSE).
