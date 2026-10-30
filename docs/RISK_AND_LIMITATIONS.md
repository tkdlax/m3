# Risk and limitations

- Paper trading does not guarantee live-trading results.
- Dividend cuts, early assignment, and gap risk can produce outcomes the model does not fully capture.
- Probability models are approximations; no Robinhood parity is claimed without independent verification.
- Cost-floor rules avoid *writing below average cost*; they do not eliminate unrealized losses.
- External data (SEC, quotes, calendars) can be stale, incomplete, or rate-limited — missing data fails closed.
- v1 has **no** live trading support; do not run with live credentials.
