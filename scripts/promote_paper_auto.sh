#!/usr/bin/env bash
# Operator helper: verify guardrails, write audit event, print promote checklist.
# Does NOT flip mode automatically in a mounted RO config — operator edits strategy.yaml.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CONFIG="${1:-config/strategy.example.yaml}"
DB="${2:-./data/agent.sqlite3}"

run() {
  if command -v uv >/dev/null 2>&1; then
    uv run "$@"
  else
    "$@"
  fi
}

echo "== promote check: $CONFIG =="
if ! run dividend-agent config validate "$CONFIG" --check-auto-submit; then
  echo
  echo "FAIL CLOSED: resolve every blocker above before setting mode: paper_auto."
  echo "Also set TRADING_PAUSED=false only when you intentionally enable submits."
  exit 1
fi

mkdir -p "$(dirname "$DB")"
run dividend-agent ledger migrate --db "$DB"
run dividend-agent ops record-promote --config "$CONFIG" --db "$DB"

echo
echo "Audit event recorded. Next steps:"
echo "  1. Edit strategy.yaml: mode: paper_auto and execution.auto_paper_orders_enabled: true"
echo "  2. Ensure every §21 numeric guardrail is non-null (see docs/OPERATIONS.md)"
echo "  3. Set TRADING_PAUSED=false in .env"
echo "  4. docker compose up -d  (or restart agent)"
echo "  5. dividend-agent broker verify-paper && scheduler run --jobs startup,trading_day"
echo
echo "Do NOT place live Alpaca credentials on the host for v1."
