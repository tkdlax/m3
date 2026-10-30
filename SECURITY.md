# Security

## Reporting

Report suspected vulnerabilities privately to the repository maintainer. Do not open public issues that include secrets, account identifiers, or holdings.

## Practice

- Never commit `.env`, API keys, or SQLite databases with account data.
- v1 is **paper-only**. Live Alpaca credentials must not be installed on deployment hosts for this release.
- Containers run as non-root with `no-new-privileges` and dropped capabilities where Compose allows.
- `TRADING_PAUSED=true` is the emergency stop for new orders (wired in later milestones; default true in Compose).

See also [docs/SECURITY.md](docs/SECURITY.md).
