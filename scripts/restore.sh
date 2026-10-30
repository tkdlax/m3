#!/usr/bin/env bash
# Restore a data (and optional reports) archive produced by scripts/backup.sh.
# Stop the agent container before restore. Does not invent live trading.
set -euo pipefail

ARCHIVE="${1:-}"
DEST_DATA="${2:-/data}"
REPORTS_ARCHIVE="${3:-}"
DEST_REPORTS="${4:-/reports}"

if [[ -z "$ARCHIVE" || ! -f "$ARCHIVE" ]]; then
  echo "Usage: $0 <agent-data-*.tar.gz> [dest_data] [reports.tar.gz] [dest_reports]" >&2
  exit 2
fi

mkdir -p "$DEST_DATA"
echo "Restoring $ARCHIVE → $DEST_DATA"
tar -xzf "$ARCHIVE" -C "$DEST_DATA"

if [[ -n "$REPORTS_ARCHIVE" ]]; then
  if [[ ! -f "$REPORTS_ARCHIVE" ]]; then
    echo "Reports archive not found: $REPORTS_ARCHIVE" >&2
    exit 1
  fi
  mkdir -p "$DEST_REPORTS"
  tar -xzf "$REPORTS_ARCHIVE" -C "$DEST_REPORTS"
  echo "Restored reports → $DEST_REPORTS"
fi

echo "Restore complete. Fix ownership to UID/GID 10001 if needed, then start the agent."
echo "  chown -R 10001:10001 $DEST_DATA ${DEST_REPORTS:-}"
