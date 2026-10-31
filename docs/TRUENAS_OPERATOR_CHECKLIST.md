# TrueNAS SCALE operator checklist (Milestone F gate)

This checklist is the **host validation** work for Milestone F. Milestone E ships readiness probes and Compose defaults that can be exercised without SCALE; **do not claim F complete** until every item below is demonstrated on the appliance.

Defaults remain `paper_dry_run` + `TRADING_PAUSED=true`. `paper_auto` submit stays gated on resolved §21 limits + unpaused + verified paper.

## Pre-flight (repo / CI — no SCALE required)

- [ ] `make check` green on the release commit
- [ ] Image builds with lockfile: `docker compose build` (Dockerfile uses `uv sync --frozen`)
- [ ] Compose config validates: `docker compose -f compose.yaml -f compose.truenas.yaml config`
- [ ] Local readiness: `dividend-agent health --readiness --db ./data/agent.sqlite3`
- [ ] Smoke: `./scripts/smoke_test.sh` (fail-closed paper verify)

## On-appliance acceptance (requires SCALE host)

1. [ ] Datasets + UID/GID **10001** per [TRUENAS_DEPLOYMENT.md](TRUENAS_DEPLOYMENT.md)
2. [ ] `.env` with `SEC_USER_AGENT`; paper keys only when intended (never live keys)
3. [ ] `docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env up -d --build`
4. [ ] Container healthy (liveness): `docker compose exec agent dividend-agent health`
5. [ ] Readiness after first scheduler tick:  
      `docker compose exec agent dividend-agent health --readiness --db /data/agent.sqlite3`
6. [ ] Default jobs include `startup,trading_day,monthly` (confirm `printenv SCHEDULER_JOBS`)
7. [ ] Unpaused dry-run still does work (reconcile, reports, cycle advances) without submits
8. [ ] `broker verify-paper` after keys; startup job completes
9. [ ] Backup / restore: `./scripts/backup.sh` + `./scripts/restore.sh` on host datasets
10. [ ] Restart resilience: `docker compose restart agent` mid-cycle; ambiguous orders sync; no duplicate submits
11. [ ] Disk exhaustion / permission failure observed and documented (operator note)
12. [ ] Upgrade / rollback: redeploy prior image tag; DB migrations forward-compatible
13. [ ] Promote path dry-run only until §21 limits set — see [OPERATIONS.md](OPERATIONS.md)

## Explicitly out of scope until checked off

Target-host readiness, backup/restore on SCALE, and rollback **are not attested by CI or Compose syntax alone**.
