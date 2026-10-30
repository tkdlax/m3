# ADR 0001: Paper-only v1

## Status

Accepted

## Context

The product must be safe to deploy on TrueNAS with Alpaca while strategy and ops limits are still being chosen.

## Decision

v1 supports only paper trading. No live endpoints, live credentials, or live-trading toggle are included. Modes are limited to `research_only`, `paper_dry_run`, and `paper_auto` (paper account only).

## Consequences

Live support requires a future explicit design and authorization. Adapters must fail closed if an environment cannot be positively verified as paper.
