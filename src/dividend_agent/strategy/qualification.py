"""Deterministic issuer qualification gates (config-driven)."""

from __future__ import annotations

from dividend_agent.config import StrategyConfig
from dividend_agent.research.citations import CriterionResult
from dividend_agent.research.memo import ResearchMemo
from dividend_agent.research.narrative import NarrativeAnalysis


def qualify_memo(
    memo: ResearchMemo,
    strategy: StrategyConfig,
    *,
    require_narrative: bool = True,
    narrative: NarrativeAnalysis | None = None,
) -> tuple[str, list[CriterionResult]]:
    """Return decision override hints from filing + narrative requirements.

    A 10-K/10-Q **link alone does not qualify**. When ``require_narrative`` is
    true, substantive extracted evidence from the filing body is required.
    """
    extra: list[CriterionResult] = []
    filings = memo.filings
    if strategy.require_latest_10k:
        if any(c.form == "10-K" or (c.form or "").startswith("10-K") for c in filings):
            extra.append(
                CriterionResult(
                    name="require_latest_10k",
                    status="pass",
                    detail="10-K reference present (narrative checked separately)",
                )
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
        if any(c.form == "10-Q" or (c.form or "").startswith("10-Q") for c in filings):
            extra.append(CriterionResult(name="require_10q", status="pass", detail="10-Q present"))
        else:
            extra.append(
                CriterionResult(
                    name="require_10q",
                    status="review",
                    detail="10-Q missing — flag for review",
                )
            )

    if require_narrative:
        if narrative is None:
            extra.append(
                CriterionResult(
                    name="filing_narrative",
                    status="fail",
                    detail="filing narrative unavailable — link alone insufficient",
                )
            )
        elif not narrative.has_substantive_evidence:
            extra.append(
                CriterionResult(
                    name="filing_narrative",
                    status="fail",
                    detail=(
                        "missing substantive narrative evidence "
                        f"(missing={list(narrative.missing_sections)})"
                    ),
                )
            )
        else:
            extra.append(
                CriterionResult(
                    name="filing_narrative",
                    status="pass",
                    detail="business+risk evidence extracted from filing body",
                )
            )

    if any(c.status == "fail" for c in extra):
        return "reject", extra
    if any(c.status == "review" for c in extra) or memo.decision == "review":
        return "review", extra
    if memo.decision == "accept" and not any(c.status == "fail" for c in memo.criteria):
        return "accept", extra
    return memo.decision, extra
