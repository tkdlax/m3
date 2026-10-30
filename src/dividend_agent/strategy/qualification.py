"""Deterministic issuer qualification gates (config-driven)."""

from __future__ import annotations

from dividend_agent.config import StrategyConfig
from dividend_agent.research.citations import CriterionResult
from dividend_agent.research.memo import ResearchMemo


def qualify_memo(memo: ResearchMemo, strategy: StrategyConfig) -> tuple[str, list[CriterionResult]]:
    """Return decision override hints from filing requirements.

    Does not invent fundamentals. Missing required filings => reject/review.
    """
    extra: list[CriterionResult] = []
    filings = memo.filings
    if strategy.require_latest_10k:
        if any(c.form == "10-K" for c in filings):
            extra.append(
                CriterionResult(name="require_latest_10k", status="pass", detail="10-K present")
            )
        else:
            extra.append(
                CriterionResult(
                    name="require_latest_10k",
                    status="fail",
                    detail="10-K missing — fail closed",
                )
            )
    if strategy.require_subsequent_10q_review:
        if any(c.form == "10-Q" for c in filings):
            extra.append(CriterionResult(name="require_10q", status="pass", detail="10-Q present"))
        else:
            extra.append(
                CriterionResult(
                    name="require_10q",
                    status="review",
                    detail="10-Q missing — flag for review",
                )
            )

    if any(c.status == "fail" for c in extra):
        return "reject", extra
    if any(c.status == "review" for c in extra) or memo.decision == "review":
        return "review", extra
    if memo.decision == "accept" and not any(c.status == "fail" for c in memo.criteria):
        return "accept", extra
    return memo.decision, extra
