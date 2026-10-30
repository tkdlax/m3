#!/usr/bin/env bash
# Local developer bootstrap (paper-only).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/" >&2
  exit 1
fi

uv sync --all-extras
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example — edit before connecting to brokers."
fi
uv run dividend-agent config validate config/strategy.example.yaml
uv run dividend-agent health
echo "Bootstrap complete. Default mode is paper_dry_run; no live trading path exists."
