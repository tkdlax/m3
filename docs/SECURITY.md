# Security (application)

- Secrets never in Git, images, prompts, or unredacted logs.
- Restrict outbound connectivity to configured paper broker, SEC, market data, and model endpoints where feasible.
- Research agent: no trading credentials or order tools (`execution.authority` asserts).
- Paper base URL must be exactly `https://paper-api.alpaca.markets` (no substring hosts).
- Startup paper attestation; recheck coverage/WAC/limits before every submit/approve/recover.
- Kill switch: `TRADING_PAUSED` / CLI pause — invalid values fail closed (paused); live env re-read on submit.
- Non-root container, read-only root filesystem, `no-new-privileges`, dropped capabilities.
- Dependency and secret scanning via CI (`.github/workflows/security.yaml`).
