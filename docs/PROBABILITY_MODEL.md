# Probability model

Status: **M3 implemented** — `bs_terminal_risk_neutral_v1` in `dividend_agent.options.probability`.

## Definitions

- **Chance of Profit (short call):** risk-neutral \(P(S_T < K + p)\) where \(p\) is **per-share** conservative premium (prefer **bid**). Fees ignored unless stated.
- **P(expire OTM):** \(P(S_T < K)\) — proxy for retaining shares at expiration under European assumptions.
- Neither equals early-assignment probability for American dividend calls. **No Robinhood parity** is claimed.

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

## Validation examples

Unit tests cover \(\Phi(0)=0.5\), deep OTM/ITM barriers, COP ≥ P(OTM) when \(p>0\), and reject-on-zero IV.
