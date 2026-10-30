# ADR 0002: Null execution guardrails fail closed

## Status

Accepted

## Context

Spec §11 / §21 leave several capital and liquidity limits for the operator. Inventing permissive defaults would be unsafe.

## Decision

Keep those fields `null` in example config. Research and `paper_dry_run` may run. Any path that would auto-submit orders must refuse while blockers remain (null limits, `auto_paper_orders_enabled=false`, mode ≠ `paper_auto`, or unverified paper).

## Consequences

Operators must set explicit numeric limits before `paper_auto`. Tests assert fail-closed behavior.
