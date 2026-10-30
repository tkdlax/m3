# Security (application)

- Secrets never in Git, images, prompts, or unredacted logs.
- Restrict outbound connectivity to configured paper broker, SEC, market data, and model endpoints where feasible.
- Research agent: no trading credentials or order tools.
- Startup paper attestation; recheck before order batches (M4+).
- Kill switch: `TRADING_PAUSED` / CLI pause.
- Non-root container, read-only root filesystem, `no-new-privileges`, dropped capabilities.
- Dependency and secret scanning via CI (`.github/workflows/security.yaml`).
