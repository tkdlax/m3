# Security (application)

- Secrets never in Git, images, prompts, or unredacted logs.
- Restrict outbound connectivity to configured paper broker, SEC, market data, and model endpoints where feasible.
- Research agent: no trading credentials or order tools.
- Chat MCP control plane (`docs/MCP_CONTROL_PLANE.md`): CLI allowlist only; no `ALPACA_*` to clients; approvals by id with `--no-submit`; localhost bind (or Bearer token) for HTTP.
- Startup paper attestation; recheck before order batches (M4+).
- Kill switch: `TRADING_PAUSED` / CLI pause.
- Non-root container, read-only root filesystem, `no-new-privileges`, dropped capabilities.
- Dependency and secret scanning via CI (`.github/workflows/security.yaml`).
