#!/usr/bin/env bash
# Backup SQLite data + optional reports/logs. Prefer online SQLite backup API
# for a consistent DB copy; fall back to tar of the data dir.
# Pausing new orders alone does not stop DB writes — stop/restart the agent
# around restore; for backup, sqlite3 .backup is consistent without full stop.
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

db_src=""
for candidate in "$SRC_DATA/agent.sqlite3" "$SRC_DATA"/*.sqlite3; do
  if [[ -f "$candidate" ]]; then
    db_src="$candidate"
    break
  fi
done

if [[ -n "$db_src" ]] && command -v sqlite3 >/dev/null 2>&1; then
  db_dest="$DEST/agent-$(basename "$db_src" .sqlite3)-$ts.sqlite3"
  # Online backup API — consistent snapshot even with WAL writers.
  sqlite3 "$db_src" ".backup '$db_dest'"
  echo "sqlite_backup=$db_dest" >>"$manifest"
  echo "Wrote consistent SQLite backup $db_dest"
else
  echo "sqlite_backup=skipped (no .sqlite3 or sqlite3 CLI)" >>"$manifest"
fi

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
