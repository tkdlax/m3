# AGENTS.md — coding agent guidance

## Navigation

| Path | Purpose |
|---|---|
| `src/dividend_agent/` | Application package |
| `src/dividend_agent/mcp_server/` | Chat MCP control plane (CLI wrappers only) |
| `config/` | Example strategy / logging YAML |
| `docs/` | Strategy, architecture, ops, MCP |
| `tests/` | unit / integration / contract / scenarios |
| `compose.yaml` | Dry-run Docker deployment |
| `compose.mcp.yaml` | Optional localhost MCP sidecar |

## Commands

```bash
uv sync --all-extras
make lint typecheck test
make config-validate
./scripts/smoke_test.sh
uv run dividend-agent broker verify-paper
uv run dividend-agent broker reconcile --db ./data/agent.sqlite3
uv run dividend-agent research screen GIS --trailing-yield 0.06 --price 40 \
  --week52-high 50 --week52-low 30 --avg-dollar-volume 1e7 --optionable
docker compose build
uv sync --extra mcp
uv run dividend-agent-mcp   # stdio MCP; see docs/MCP_CONTROL_PLANE.md
```

## Style

- Python 3.12+, type hints, Pydantic models for config/domain.
- `Decimal` for money; never float for strikes/premiums in logic (M3+).
- Fail closed: missing data, unverified paper, null auto-submit guardrails.
- Do not invent permissive defaults for unresolved decisions in `docs/DECISION_POLICY.md` (§21).

## Constraints

- Paper-only v1 — no live trading code paths.
- Keep secrets out of the repo; use `.env.example` placeholders only.
- Milestone-sized PRs; include tests for guardrail behavior.

## PR checklist

1. `make check` green
2. Smoke script still expects fail-closed paper verify
3. Docs updated if policy/ops change
4. No claim of production readiness without paper integration tests
