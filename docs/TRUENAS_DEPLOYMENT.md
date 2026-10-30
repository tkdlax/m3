# TrueNAS SCALE deployment

**Scope:** TrueNAS **SCALE** (or editions with first-party Docker/Compose Apps).  
**Out of scope:** TrueNAS **CORE** Docker plugins — do not assume CORE parity.

Documented installation path: **Custom App / Compose** using `compose.yaml` + optional `compose.truenas.yaml`.

**Validated against:** TrueNAS SCALE **24.10+** / **25.04+** Compose-capable Apps (operator should confirm their exact SCALE build). This repository ships the manifests; host validation is an operator step on the appliance.

## Safety

- Paper-only v1. **Do not place live Alpaca credentials on the host.**
- Default `TRADING_PAUSED=true` and `mode: paper_dry_run`.
- No inbound published ports for MVP (no unauthenticated HTTP).
- Container: non-root UID **10001**, read-only root FS, `no-new-privileges`, all capabilities dropped.

## 1. Datasets and permissions

Create a parent dataset, e.g. `tank/apps/dividend-agent`, with children:

| Dataset / path | Mount | Notes |
|---|---|---|
| `.../data` | `/data` | SQLite WAL DB |
| `.../reports` | `/reports` | monthly reports + alert JSON |
| `.../logs` | `/logs` | reserved for operator log capture |
| `.../config` | host file → `/config/strategy.yaml` | private strategy (not the example forever) |

```bash
# On TrueNAS (paths illustrative)
install -d -o 10001 -g 10001 -m 0750 /mnt/tank/apps/dividend-agent/{data,reports,logs,config}
cp strategy.example.yaml /mnt/tank/apps/dividend-agent/config/strategy.yaml
chown 10001:10001 /mnt/tank/apps/dividend-agent/config/strategy.yaml
chmod 0640 /mnt/tank/apps/dividend-agent/config/strategy.yaml
```

ACL tip: grant the Apps user **UID/GID 10001** modify on data/reports/logs; config may be root-owned read-only for the container.

## 2. Secrets and env

```bash
cp .env.example .env
# Edit: SEC_USER_AGENT, ALPACA_PAPER_* (paper only), TRUENAS_* paths
# Never commit .env. Never add live API keys.
```

Store `.env` on the host dataset or in SCALE secret fields — not in Git.
Secrets (`ALPACA_PAPER_*`, `SEC_USER_AGENT`) must be in `.env`; do not export empty `ALPACA_*` in the shell (Compose would otherwise pass blanks into the container).

## 3. Bring up (dry-run)

From the cloned repo on the SCALE host (or a jump box that can drive Compose against the Apps runtime):

```bash
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env up -d --build
docker compose ps
docker compose logs -f agent
docker compose exec agent dividend-agent health
docker compose exec agent dividend-agent config validate /config/strategy.yaml
```

Without paper keys, the container stays healthy; startup scheduler cycles may log paper-unverified failures until keys are set. Set `REQUIRE_PAPER_ON_START=true` only after keys work.

## 4. Operator acceptance path (no source edits)

1. `config validate /config/strategy.yaml`
2. Set paper keys → `broker verify-paper`
3. `ledger migrate` (entrypoint already migrates)
4. Run dry-run cycles: `scheduler run --jobs startup,trading_day,monthly --reports /reports`
5. Inspect `/reports` and `approvals list` / `scheduler status`
6. Set every null §21 guardrail in `strategy.yaml` (see [OPERATIONS.md](OPERATIONS.md))
7. Promote: `scripts/promote_paper_auto.sh /config/strategy.yaml /data/agent.sqlite3` (or `ops record-promote` after mode/limits are set)
8. Set `TRADING_PAUSED=false` only when intentionally enabling paper submits
9. Confirm backups, healthchecks, time sync (UTC internal; `TZ=America/Denver` display)

## 5. Backup and restore

```bash
# From host, with datasets mounted or via exec
./scripts/backup.sh /mnt/tank/apps/dividend-agent/data ./backup \
  /mnt/tank/apps/dividend-agent/reports \
  /mnt/tank/apps/dividend-agent/logs

# Emergency restore (stop agent first)
docker compose stop agent
./scripts/restore.sh ./backup/agent-data-YYYYMMDD….tar.gz \
  /mnt/tank/apps/dividend-agent/data \
  ./backup/agent-reports-….tar.gz \
  /mnt/tank/apps/dividend-agent/reports
chown -R 10001:10001 /mnt/tank/apps/dividend-agent/{data,reports}
docker compose start agent
```

Prefer SCALE periodic snapshots of the datasets in addition to tar backups.

## 6. Upgrade

```bash
git fetch && git checkout <release-tag>   # or pull a versioned image tag
cp .env.example .env   # only merge new keys; do not clobber secrets
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env pull  # if using registry
docker compose -f compose.yaml -f compose.truenas.yaml --env-file .env up -d --build
docker compose exec agent dividend-agent ledger migrate --db /data/agent.sqlite3
docker compose exec agent dividend-agent health
```

Migrations are additive SQLite files under `src/dividend_agent/storage/migrations/`.

## 7. Rollback

1. `docker compose stop agent`
2. Restore previous image tag (`IMAGE_TAG=…`) or prior Git revision
3. If schema moved forward incompatibly (rare in v1), restore the last `agent-data-*.tar.gz` taken **before** the upgrade
4. `docker compose up -d`
5. Verify `health` and `broker verify-paper`

## 8. Emergency stop

```bash
# Kill switch (preferred — keeps research/reporting)
# In .env: TRADING_PAUSED=true
docker compose up -d

# Hard stop
docker compose stop agent
# or: docker stop dividend-agent

docker compose exec agent dividend-agent ops emergency-stop   # prints checklist
```

Revoke paper API keys in the Alpaca dashboard if compromise is suspected. Rebuild/recreate the container after rotating secrets.

## 9. Logs and health

```bash
docker compose logs --tail=200 agent
docker compose ps
docker inspect --format='{{.State.Health.Status}}' dividend-agent
```

## 10. What not to do

- Do not publish host ports for an unauthenticated UI in v1.
- Do not mount the Docker socket or run privileged.
- Do not use host networking.
- Do not put live trading credentials on the host.
- Do not assume TrueNAS CORE “Plugins” Docker behaves like SCALE Apps.
