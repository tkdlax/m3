# MCP control plane (chat agents → CLI)

Grok, Claude, and Cursor can trigger **research and jobs** through this MCP server.
Every tool shells into the `dividend-agent` CLI (local binary or `docker exec`).
Chat agents never receive Alpaca credentials or a raw order API.

## Security model vs Alpaca MCP

| Plane | Role | Secrets | Orders |
|---|---|---|---|
| **This control plane** (chat MCP) | Research, validate, reconcile, scheduler, approvals-by-id, status | No `ALPACA_*` for the MCP client | No submit-order tool; approvals always `--no-submit` |
| **Official Alpaca MCP** (spec §7.1) | Preferred *inside* `dividend-agent` broker adapter later | Stays in the agent/broker process | May place **paper** orders only after deterministic gates |
| **Alpaca REST adapter** (current) | `AlpacaPaperAdapter` behind `BrokerAdapter` | `ALPACA_PAPER_*` in agent `.env` only | Paper host only; kill switch `TRADING_PAUSED` |

Hard blocks on the chat MCP:

- No tool that submits arbitrary orders
- No free-text “approve this trade” — `approvals_approve` / `approvals_reject` require a concrete `approval_id`
- No live trading path
- No passthrough of `ALPACA_*` secrets to the MCP client (docker backend strips them from the MCP process env; tool stdout/stderr redacts secret values)

## Tools (allowlist)

| Tool | CLI |
|---|---|
| `health` | `dividend-agent health` |
| `config_validate` | `config validate` |
| `broker_verify_paper` | `broker verify-paper` |
| `broker_reconcile` | `broker reconcile` |
| `research_screen` | `research screen` |
| `research_memo` | `research symbol` |
| `options_evaluate` | `options evaluate` |
| `scheduler_run` | `scheduler run` |
| `scheduler_status` | `scheduler status` |
| `approvals_list` | `approvals list` |
| `approvals_approve` | `approvals approve <id> --no-submit` |
| `approvals_reject` | `approvals reject <id>` |
| `status_summary` | `reports summary` |

Absent by design: `trade *`, `ops *`, `submit_*`, Alpaca MCP passthrough.

## Install (host / laptop)

```bash
uv sync --extra mcp --all-extras
# or: pip install '.[mcp]'
```

## Stdio (Cursor / Claude Desktop) — preferred

Point the client at the module entrypoint. The MCP process runs `dividend-agent` locally **or** `docker exec` into the agent container.

### Local CLI (dev checkout)

```json
{
  "mcpServers": {
    "dividend-agent": {
      "command": "uv",
      "args": ["run", "--extra", "mcp", "dividend-agent-mcp"],
      "cwd": "/path/to/m3",
      "env": {
        "DIVIDEND_AGENT_MCP_BACKEND": "local",
        "DIVIDEND_AGENT_CONFIG_PATH": "config/strategy.example.yaml",
        "DIVIDEND_AGENT_DB_PATH": "./data/agent.sqlite3",
        "DIVIDEND_AGENT_REPORTS_DIR": "./reports"
      }
    }
  }
}
```

Claude Desktop: same JSON under `mcpServers` in its config file.

### Docker exec (agent already running)

```json
{
  "mcpServers": {
    "dividend-agent": {
      "command": "uv",
      "args": ["run", "--extra", "mcp", "dividend-agent-mcp"],
      "cwd": "/path/to/m3",
      "env": {
        "DIVIDEND_AGENT_MCP_BACKEND": "docker",
        "DIVIDEND_AGENT_MCP_CONTAINER": "dividend-agent",
        "DIVIDEND_AGENT_CONFIG_PATH": "/config/strategy.yaml",
        "DIVIDEND_AGENT_DB_PATH": "/data/agent.sqlite3",
        "DIVIDEND_AGENT_REPORTS_DIR": "/reports"
      }
    }
  }
}
```

Grok / other MCP clients: use the same stdio command or the HTTP URL below when a loopback sidecar is running.

## Optional Compose HTTP sidecar (localhost)

```bash
docker compose build
docker compose -f compose.yaml -f compose.mcp.yaml --env-file .env up -d --build
```

- Listens on **`127.0.0.1:8765`** only (`MCP_TRANSPORT=streamable-http`)
- Does **not** mount `env_file` — no Alpaca keys in the MCP container
- Uses `docker.sock` to `docker exec dividend-agent …`
- Non-loopback bind without `MCP_AUTH_TOKEN` **refuses to start**
- With `MCP_AUTH_TOKEN` set, HTTP requests need `Authorization: Bearer <token>`

Cursor HTTP example (local sidecar):

```json
{
  "mcpServers": {
    "dividend-agent": {
      "url": "http://127.0.0.1:8765/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_AUTH_TOKEN}"
      }
    }
  }
}
```

Exact path may vary by client (`/mcp` for streamable HTTP). Prefer stdio when the client supports it.

## TrueNAS paths

Pool preference (this deployment): **`/mnt/RJMSA_Nas`** (not `tank`).

| Host path | Role |
|---|---|
| `/mnt/RJMSA_Nas/dividend-agent/src` | Git checkout / Compose project (operator clone) |
| `/mnt/RJMSA_Nas/dividend-agent/config/strategy.yaml` | Private strategy → `/config/strategy.yaml` |
| `/mnt/RJMSA_Nas/dividend-agent/data` | SQLite → `/data` |
| `/mnt/RJMSA_Nas/dividend-agent/reports` | Reports → `/reports` |
| `/mnt/RJMSA_Nas/dividend-agent/logs` | Logs → `/logs` |

Set in `.env` (see `compose.truenas.yaml`):

```bash
TRUENAS_DATA_PATH=/mnt/RJMSA_Nas/dividend-agent/data
TRUENAS_REPORTS_PATH=/mnt/RJMSA_Nas/dividend-agent/reports
TRUENAS_LOGS_PATH=/mnt/RJMSA_Nas/dividend-agent/logs
TRUENAS_CONFIG_PATH=/mnt/RJMSA_Nas/dividend-agent/config/strategy.yaml
```

Bring-up with MCP:

```bash
cd /mnt/RJMSA_Nas/dividend-agent/src
docker compose -f compose.yaml -f compose.truenas.yaml -f compose.mcp.yaml \
  --env-file .env up -d --build
docker compose exec agent dividend-agent health
# MCP is on the TrueNAS host loopback only — SSH tunnel to use from a laptop:
#   ssh -L 8765:127.0.0.1:8765 truenas
```

Agent container name: **`dividend-agent`**.

## Operator notes

- Approving via MCP marks the exception; it does **not** submit paper orders (`--no-submit`).
- Scheduler / reconcile still run inside the agent where paper verification and kill switch apply.
- See also [ALPACA_INTEGRATION.md](ALPACA_INTEGRATION.md), [OPERATIONS.md](OPERATIONS.md), and [SECURITY.md](SECURITY.md).
