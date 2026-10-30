# Strategy

Faithful summary of the investor philosophy. Implementation defaults that are still **open** are listed in [DECISION_POLICY.md](DECISION_POLICY.md) — do not treat this page as authorizing invented limits.

## Intent

- Screen established dividend-paying U.S. equities with indicative yields roughly **5%–7%**; unusually high yields (e.g. 9%+) warrant review, not blind buying.
- Prefer accumulating **100 shares** per issuer for one standard covered call; partial lots are allowed when capital is short.
- Sell **fully covered** calls (~30 DTE), strike at/above **weighted-average acquisition cost**, normally OTM, targeting modeled Chance of Profit ≥ **90%** (aggressive 80%–<90% regime off by default).
- No forced monthly trading; holding for dividends is valid when no qualifying call exists.
- Avoid opening calls that span a known ex-dividend date unless an **explicit, tested** policy later permits it (currently `avoid_overlap`).
- M3 evaluates chains into explainable `TradeIntent`s; **order submission is M4**. Adjusted deliverables are rejected until fully supported.

## Non-guarantees

Capital preservation preference and strike floors do **not** prevent market losses. Chance of Profit is not probability of retaining shares or of a profitable combined position. Target income bands are aspirations, not floors.

## Illustrative names

GIS, T, PFE were mentioned historically as examples — **none are preapproved**.
