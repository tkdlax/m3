# TrueNAS SCALE operator checklist (Jake host — Milestone F)

**Host paths (Jake):** `/mnt/RJMSA_Nas/dividend-agent/…`  
**This document does not claim SCALE was validated on hardware.** Check every box on the appliance before treating F as complete.

Defaults remain `paper_dry_run` + `TRADING_PAUSED=true`. `paper_auto` submit stays gated on resolved §21 limits + unpaused + verified paper.

Canonical product requirements: [PROJECT_SPEC.md](PROJECT_SPEC.md). Deployment overview: [TRUENAS_DEPLOYMENT.md](TRUENAS_DEPLOYMENT.md).

## Pre-flight (repo / CI — no SCALE required)

- [ ] `make check` green on the release commit
- [ ] Image builds with lockfile: `docker compose build` (Dockerfile uses `uv sync --frozen`)
- [ ] Compose config validates:
  ```bash
  docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env config
  ```
- [ ] Local readiness after a scheduler tick:
  ```bash
  dividend-agent health --db ./data/agent.sqlite3
  dividend-agent health --readiness --db ./data/agent.sqlite3
  ```
  Operational health must **not** report `status=ok` when recent jobs/cycles failed (`unhealthy` / readiness `not_ready`).
- [ ] Process liveness (Docker HEALTHCHECK equivalent): `dividend-agent health --liveness`
- [ ] Smoke: `./scripts/smoke_test.sh` (fail-closed paper verify)

## 1. Datasets and permissions (Jake)

```bash
install -d -o 10001 -g 10001 -m 0750 \
  /mnt/RJMSA_Nas/dividend-agent/{data,reports,logs,config,backup}
cp config/strategy.example.yaml /mnt/RJMSA_Nas/dividend-agent/config/strategy.yaml
chown 10001:10001 /mnt/RJMSA_Nas/dividend-agent/config/strategy.yaml
chmod 0640 /mnt/RJMSA_Nas/dividend-agent/config/strategy.yaml
```

`.env` on the host (never commit; paper keys only when intended):

```bash
# Suggested paths for compose.truenas.yaml
TRUENAS_DATA_PATH=/mnt/RJMSA_Nas/dividend-agent/data
TRUENAS_REPORTS_PATH=/mnt/RJMSA_Nas/dividend-agent/reports
TRUENAS_LOGS_PATH=/mnt/RJMSA_Nas/dividend-agent/logs
TRUENAS_CONFIG_PATH=/mnt/RJMSA_Nas/dividend-agent/config/strategy.yaml
```

- [ ] Datasets exist; UID/GID **10001** can write data/reports/logs
- [ ] `.env` has `SEC_USER_AGENT`; **no live Alpaca keys**

## 2. Startup / shutdown / restart

From the cloned repo on SCALE (or jump box driving Compose):

```bash
cd /path/to/m3   # repo checkout on host
cp .env.example .env   # then edit paths + secrets
# set TRUENAS_* to /mnt/RJMSA_Nas/dividend-agent/... as above

docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env up -d --build
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env ps
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env logs -f agent
```

Liveness vs operational:

```bash
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env \
  exec agent dividend-agent health --liveness
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env \
  exec agent dividend-agent health --db /data/agent.sqlite3
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env \
  exec agent dividend-agent health --readiness --db /data/agent.sqlite3
```

Shutdown / restart:

```bash
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env stop
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env start
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env restart agent
```

- [ ] Container starts; HEALTHCHECK uses `--liveness`
- [ ] After first scheduler tick, operational health / readiness reflect job state
- [ ] `printenv SCHEDULER_JOBS` inside container shows `startup,trading_day,monthly`
- [ ] Restart mid-cycle: ambiguous orders sync; no duplicate submits under dry_run

## 3. Backup / restore

Prefer stopping the agent for restore. Backup uses SQLite online `.backup` when `sqlite3` is available (consistent under WAL), plus a tar of the data dir.

```bash
# Backup (from host; adjust DEST)
./scripts/backup.sh \
  /mnt/RJMSA_Nas/dividend-agent/data \
  /mnt/RJMSA_Nas/dividend-agent/backup \
  /mnt/RJMSA_Nas/dividend-agent/reports \
  /mnt/RJMSA_Nas/dividend-agent/logs

# Restore — stop agent first
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env stop
./scripts/restore.sh \
  /mnt/RJMSA_Nas/dividend-agent/backup/agent-data-YYYYMMDDThhmmssZ.tar.gz \
  /mnt/RJMSA_Nas/dividend-agent/data \
  /mnt/RJMSA_Nas/dividend-agent/backup/agent-reports-YYYYMMDDThhmmssZ.tar.gz \
  /mnt/RJMSA_Nas/dividend-agent/reports
chown -R 10001:10001 /mnt/RJMSA_Nas/dividend-agent/data /mnt/RJMSA_Nas/dividend-agent/reports
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env start
```

If a consistent `.sqlite3` file was written by `backup.sh`, you may also replace `agent.sqlite3` with that file while stopped.

- [ ] Backup produces manifest + sqlite backup and/or tar
- [ ] Restore + chown + start recovers scheduler status
- [ ] Disk-full / permission denial observed and noted by operator

## 4. Upgrade / rollback

```bash
# Record running image
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env images

# Upgrade
git fetch && git checkout <release-tag-or-commit>
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env up -d --build

# Rollback: check out prior tag/commit, rebuild/redeploy same compose files
# DB migrations are forward-additive; do not invent down-migrations.
```

- [ ] Upgrade keeps data mounts; startup migrates schema
- [ ] Rollback to prior image/tag demonstrated
- [ ] Promote path stays dry-run until §21 limits set — [OPERATIONS.md](OPERATIONS.md)

## 5. Paper verify (optional on host)

```bash
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env \
  exec agent dividend-agent broker verify-paper
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env \
  exec agent dividend-agent scheduler run --jobs startup,trading_day,monthly \
  --db /data/agent.sqlite3 --reports /reports --config /config/strategy.yaml
```

- [ ] Unpaused dry-run does work (reconcile, reports, cycle advances) without submits
- [ ] `broker verify-paper` after keys; startup job completes

## Explicitly out of scope until checked off on Jake’s SCALE

Target-host readiness, backup/restore on the appliance, restart/permissions/disk exhaustion, and rollback **are not attested by CI or Compose syntax alone**. This repo ships the commands; Jake (or another operator) must execute them on `/mnt/RJMSA_Nas/dividend-agent`.
