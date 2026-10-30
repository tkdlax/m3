# TrueNAS deployment

Target: TrueNAS **SCALE** (or editions with supported Docker/Compose). Do not assume CORE parity.

## Outline

1. Create datasets for `data`, `reports`, `logs`, and a private config path; set UID/GID **10001** to match the image user (or adjust Compose).
2. Clone this repo or pull a versioned image; copy `.env.example` → `.env` and `strategy.example.yaml` → private `strategy.yaml`.
3. Set paper credentials + `SEC_USER_AGENT` only. **No live keys.**
4. `docker compose -f compose.yaml -f compose.truenas.yaml up -d --build`
5. Run `config validate` and (M1+) `broker verify-paper` inside the container.
6. Remain on `paper_dry_run` until every null guardrail is explicitly set; promote only with audit.

Tested TrueNAS release(s) will be recorded here after M6 validation — currently **unverified / open**.
