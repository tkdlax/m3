#!/usr/bin/env sh
# Container entrypoint: dry-run safe boot, optional scheduler loop (paper-only).
set -eu

CONFIG_PATH="${CONFIG_PATH:-/config/strategy.yaml}"
FALLBACK_CONFIG="/app/config/strategy.example.yaml"
DB_PATH="${DB_PATH:-/data/agent.sqlite3}"
REPORTS_DIR="${REPORTS_DIR:-/reports}"
SCHEDULER_ENABLED="${SCHEDULER_ENABLED:-true}"
SCHEDULER_INTERVAL_SEC="${SCHEDULER_INTERVAL_SEC:-3600}"
REQUIRE_PAPER_ON_START="${REQUIRE_PAPER_ON_START:-false}"
SCHEDULER_JOBS="${SCHEDULER_JOBS:-startup,trading_day,monthly}"

echo "[entrypoint] dividend-agent boot (paper-only; TRADING_PAUSED=${TRADING_PAUSED:-unset})"

if [ -f "$CONFIG_PATH" ]; then
  dividend-agent config validate "$CONFIG_PATH"
  ACTIVE_CONFIG="$CONFIG_PATH"
else
  echo "[entrypoint] $CONFIG_PATH missing; using example config (dry-run)"
  dividend-agent config validate "$FALLBACK_CONFIG"
  ACTIVE_CONFIG="$FALLBACK_CONFIG"
fi

dividend-agent ledger migrate --db "$DB_PATH"
dividend-agent health --config "$ACTIVE_CONFIG"

if [ "$REQUIRE_PAPER_ON_START" = "true" ]; then
  dividend-agent broker verify-paper
fi

if [ "$SCHEDULER_ENABLED" != "true" ]; then
  echo "[entrypoint] SCHEDULER_ENABLED=false — idling (CLI available via exec)"
  exec sleep infinity
fi

echo "[entrypoint] scheduler loop jobs=${SCHEDULER_JOBS} interval=${SCHEDULER_INTERVAL_SEC}s"
while true; do
  # Failures (e.g. missing paper creds on startup job) must not kill the container.
  if ! dividend-agent scheduler run \
    --jobs "$SCHEDULER_JOBS" \
    --config "$ACTIVE_CONFIG" \
    --db "$DB_PATH" \
    --reports "$REPORTS_DIR"; then
    echo "[entrypoint] scheduler cycle failed (will retry); check logs / notifications" >&2
  fi
  sleep "$SCHEDULER_INTERVAL_SEC"
done
