#!/usr/bin/env bash
# Backup SQLite data + optional reports/logs. Safe for operator cron on TrueNAS.
set -euo pipefail

SRC_DATA="${1:-/data}"
DEST="${2:-./backup}"
SRC_REPORTS="${3:-}"
SRC_LOGS="${4:-}"

mkdir -p "$DEST"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
manifest="$DEST/backup-$ts.manifest.txt"

{
  echo "created_at_utc=$ts"
  echo "src_data=$SRC_DATA"
} >"$manifest"

if [[ ! -d "$SRC_DATA" ]]; then
  echo "No data directory at $SRC_DATA; nothing to back up." >&2
  exit 1
fi

# Brief quiesce tip: stop or pause the agent for a clean SQLite copy when possible.
archive="$DEST/agent-data-$ts.tar.gz"
tar -czf "$archive" -C "$SRC_DATA" .
echo "data_archive=$archive" >>"$manifest"
echo "Wrote $archive"

if [[ -n "$SRC_REPORTS" && -d "$SRC_REPORTS" ]]; then
  tar -czf "$DEST/agent-reports-$ts.tar.gz" -C "$SRC_REPORTS" .
  echo "reports_archive=$DEST/agent-reports-$ts.tar.gz" >>"$manifest"
  echo "Wrote $DEST/agent-reports-$ts.tar.gz"
fi

if [[ -n "$SRC_LOGS" && -d "$SRC_LOGS" ]]; then
  tar -czf "$DEST/agent-logs-$ts.tar.gz" -C "$SRC_LOGS" .
  echo "logs_archive=$DEST/agent-logs-$ts.tar.gz" >>"$manifest"
  echo "Wrote $DEST/agent-logs-$ts.tar.gz"
fi

echo "Manifest: $manifest"
