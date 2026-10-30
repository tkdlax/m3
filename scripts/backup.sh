#!/usr/bin/env bash
# Placeholder backup helper for SQLite + reports (expanded in M6).
set -euo pipefail
SRC_DATA="${1:-/data}"
DEST="${2:-./backup}"
mkdir -p "$DEST"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -d "$SRC_DATA" ]]; then
  tar -czf "$DEST/agent-data-$ts.tar.gz" -C "$SRC_DATA" .
  echo "Wrote $DEST/agent-data-$ts.tar.gz"
else
  echo "No data directory at $SRC_DATA; nothing to back up." >&2
  exit 1
fi
