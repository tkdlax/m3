# Probability model

Status: **M3 implemented** — `bs_terminal_risk_neutral_v1` in `dividend_agent.options.probability`.

## Metric glossary (handoff §8)

| Metric | Symbol / formula | What it is | What it is **not** |
|---|---|---|---|
| **Chance of Profit (COP)** | \(P(S_T < K + p)\) | Risk-neutral probability that terminal spot is below strike **plus** per-share premium (short-call cash-secured breakeven under European assumptions) | Combined stock+call position P&L; physical-measure “will I make money”; early-assignment probability |
| **P(expire OTM)** | \(P(S_T < K)\) | Risk-neutral probability spot finishes below strike (shares retained at expiry under European assumptions) | COP; combined-position profitability |
| **Position / combined P&L** | Stock gain/loss ± premium ± dividends − fees | Accounting outcome of the covered pair over a holding period | Not computed by the probability model; reports must not equate COP or OTM to this |
| **Early assignment** | American exercise (esp. near ex-div) | Separate operational risk | **Not** estimated by COP or P(OTM) |

**Strategy threshold:** the configured **90%** floor (`covered_calls.chance_of_profit_min`, default `0.90`) refers to **COP** = \(P(S_T < K + p)\), **not** P(OTM) and **not** combined P&L. The 80%–&lt;90% band is **review / approval-eligible** when `aggressive_mode_enabled` is false (gate emits review rather than auto-accept); enabling aggressive mode does not bypass hard blocks.

Candidate intents record both `chance_of_profit` and `prob_expire_otm` plus an explicit `metric_note` so reports and decisions keep them distinct.

## Definitions

- **Chance of Profit (short call):** risk-neutral \(P(S_T < K + p)\) where \(p\) is **per-share** conservative premium (prefer **bid**). Fees ignored unless stated.
- **P(expire OTM):** \(P(S_T < K)\) — proxy for retaining shares at expiration under European assumptions.
- Neither equals early-assignment probability for American dividend calls. **No Robinhood parity** is claimed.
- A conservative `avoid_overlap` ex-dividend policy skips spanning calls; that is an operational choice, **not** a model of assignment probability.

## Model

Black–Scholes-style lognormal terminal distribution:

\[
d_2 = \frac{\ln(S/X) + (r - q - \tfrac{1}{2}\sigma^2)T}{\sigma\sqrt{T}}, \quad
P(S_T < X) = \Phi(-d_2)
\]

- \(T\): calendar days to expiry / 365
- \(\sigma\): observed implied volatility (required; missing ⇒ fail closed)
- \(r\): risk-free rate (default 0.05 when not supplied)
- \(q\): continuous dividend yield (default **0 if unknown** — documented assumption)
- Premium stored as **per-share** and **per-contract** (\(p \times 100\))

## Limitations

- European terminal approximation; does not price American early exercise.
- Risk-neutral, not real-world physical measure.
- No volatility skew surface; single IV input.
- Dividend yield often unknown at evaluation time → assumed zero rather than invented nonzero.
- Quote-aware premium uses bid; midpoint/last alone are not used for COP inputs.
- Combined covered-call P&L and verified ex-date assignment monitoring are reporting/ops concerns (Priority 2 / later milestones), not COP substitutes.

## Validation examples

Unit tests cover \(\Phi(0)=0.5\), deep OTM/ITM barriers, COP ≥ P(OTM) when \(p>0\), and reject-on-zero IV.
