#!/usr/bin/env bash
# M0 smoke: config validate, health/version, broker verify-paper fails closed.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

run() {
  if command -v uv >/dev/null 2>&1; then
    uv run "$@"
  else
    "$@"
  fi
}

echo "== version =="
run dividend-agent version

echo "== health =="
run dividend-agent health --config config/strategy.example.yaml

echo "== config validate =="
run dividend-agent config validate config/strategy.example.yaml

echo "== config validate --check-auto-submit (expect fail) =="
if run dividend-agent config validate config/strategy.example.yaml --check-auto-submit; then
  echo "expected auto-submit check to fail closed" >&2
  exit 1
fi

echo "== broker verify-paper (expect fail closed) =="
if run dividend-agent broker verify-paper; then
  echo "expected broker verify-paper to fail closed without verified paper" >&2
  exit 1
fi

echo "Smoke OK (fail-closed paths behaved as expected)."
