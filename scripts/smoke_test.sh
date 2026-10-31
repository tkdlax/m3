#!/usr/bin/env bash
# Smoke: config validate, health/version, broker fail-closed without creds, ledger migrate.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Ensure fail-closed path is exercised even if the shell has leftover secrets.
unset ALPACA_PAPER_API_KEY ALPACA_PAPER_API_SECRET || true

run() {
  if command -v uv >/dev/null 2>&1; then
    uv run "$@"
  else
    "$@"
  fi
}

echo "== version =="
run dividend-agent version

echo "== health (liveness + local without DB) =="
run dividend-agent health --liveness
run dividend-agent health --config config/strategy.example.yaml

echo "== config validate =="
run dividend-agent config validate config/strategy.example.yaml

echo "== config validate --check-auto-submit (expect fail) =="
if run dividend-agent config validate config/strategy.example.yaml --check-auto-submit; then
  echo "expected auto-submit check to fail closed" >&2
  exit 1
fi

echo "== broker verify-paper (expect fail closed without creds) =="
if run dividend-agent broker verify-paper; then
  echo "expected broker verify-paper to fail closed without verified paper" >&2
  exit 1
fi

echo "== broker reconcile (expect fail closed without creds) =="
SMOKE_DB="${TMPDIR:-/tmp}/dividend-agent-smoke.sqlite3"
rm -f "$SMOKE_DB"
if run dividend-agent broker reconcile --db "$SMOKE_DB"; then
  echo "expected broker reconcile to fail closed without verified paper" >&2
  exit 1
fi

echo "== ledger migrate =="
run dividend-agent ledger migrate --db "$SMOKE_DB"

echo "== operational health after migrate (may be degraded; must not crash) =="
run dividend-agent health --db "$SMOKE_DB" || true
run dividend-agent health --readiness --db "$SMOKE_DB" || true

echo "Smoke OK (fail-closed paths behaved as expected)."
