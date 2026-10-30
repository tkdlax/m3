# Contributing

## Scope

This repository builds a **paper-only** dividend + covered-call agent. Do not add live brokerage routes, live credentials, or a live-trading toggle in v1.

## Development

```bash
# Requires Python 3.12+ and uv (https://docs.astral.sh/uv/)
./scripts/bootstrap.sh
make check
```

## Guidelines

- Prefer milestone-sized PRs aligned with `docs/` and the product spec.
- Fail closed on missing data, unverified paper accounts, and null §21 guardrails for auto-submit.
- Do not invent permissive defaults for unresolved operator decisions (see `docs/DECISION_POLICY.md`).
- Use `Decimal` for money; keep secrets out of Git and logs.
- Research agents must never receive order-submission tools or credentials.

## PR checklist

- [ ] Tests added/updated for behavioral changes
- [ ] `make check` passes locally
- [ ] Docs updated when policy or ops change
- [ ] Paper-trading impact called out in the PR template
