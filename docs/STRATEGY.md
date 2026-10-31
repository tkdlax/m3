# Strategy

Faithful summary of the investor philosophy. Implementation defaults that are still **open** are listed in [DECISION_POLICY.md](DECISION_POLICY.md) — do not treat this page as authorizing invented limits.

## Intent

- Screen established dividend-paying U.S. equities with indicative yields roughly **5%–7%**; unusually high yields (e.g. 9%+) warrant review, not blind buying.
- Prefer accumulating **100 shares** per issuer for one standard covered call; partial lots are allowed when capital is short.
- Sell **fully covered** calls (~30 DTE), strike at/above **weighted-average acquisition cost**, normally OTM, targeting modeled **Chance of Profit (COP)** ≥ **90%** — where COP means risk-neutral \(P(S_T < K + p)\), **not** P(expire OTM) and **not** combined stock+call P&L (see [PROBABILITY_MODEL.md](PROBABILITY_MODEL.md)). Aggressive 80%–&lt;90% COP is review/approval-eligible and off by default.
- No forced monthly trading; holding for dividends is valid when no qualifying call exists.
- Avoid opening calls that span a known ex-dividend date unless an **explicit, tested** policy later permits it (currently `avoid_overlap`). That skip is **not** a model of early-assignment probability.
- Candidate selection ranks accepted calls and **allocates without overcommitting** unencumbered shares (executable vs alternatives).
- M3 evaluates chains into explainable `TradeIntent`s; **order submission is M4**. Adjusted deliverables are rejected until fully supported.

## Non-guarantees

Capital preservation preference and strike floors do **not** prevent market losses. **COP ≠ P(OTM) ≠ combined position P&L.** Target income bands are aspirations, not floors.

## Illustrative names

GIS, T, PFE were mentioned historically as examples — **none are preapproved**.
