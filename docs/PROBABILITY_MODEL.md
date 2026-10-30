# Probability model

Status: **stub** — equations and validation land in M3.

## Definitions (intended)

- **Chance of Profit (short call):** approximate probability underlying settlement is below `strike + premium_per_share` (fees ignored unless stated).
- **P(expire OTM):** more direct proxy for retaining shares at expiration.
- Neither equals early-assignment probability for American dividend calls.

## Requirements when implemented

- Prefer observed IV, time, rates, dividend-aware adjustments; document risk-neutral vs real-world assumptions.
- Store premium **per-share** and **per-contract**.
- Use quote-aware executable premium (e.g. bid / conservative limit), not last trade alone.
- No claim of exact Robinhood parity without independent verification.
- Test against analytic cases and representative broker displays.
