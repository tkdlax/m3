"""End-to-end issuer research pipeline (SEC + screen + memo). No trading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dividend_agent.agents.interface import ResearchAgent
from dividend_agent.agents.research_agent import DeterministicResearchAgent
from dividend_agent.config import AppConfig
from dividend_agent.research.company_facts import extract_company_facts
from dividend_agent.research.dividends import extract_dividends_from_companyfacts
from dividend_agent.research.memo import IssuerIdentity, ResearchMemo, build_deterministic_memo
from dividend_agent.research.screener import MarketSnapshotInput, screen_issuer
from dividend_agent.research.sec_edgar import SecEdgarClient, SecEdgarError
from dividend_agent.strategy.qualification import qualify_memo


@dataclass
class ResearchPipeline:
    config: AppConfig
    sec: SecEdgarClient
    agent: ResearchAgent

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        cache_dir: str | Path | None = None,
        sec_client: SecEdgarClient | None = None,
        agent: ResearchAgent | None = None,
    ) -> ResearchPipeline:
        user_agent = (
            config.research.sec_user_agent or os.environ.get("SEC_USER_AGENT", "")
        ).strip()
        client = sec_client or SecEdgarClient(
            user_agent,
            cache_dir=cache_dir,
        )
        return cls(
            config=config,
            sec=client,
            agent=agent or DeterministicResearchAgent(),
        )

    def research_symbol(
        self,
        ticker: str,
        *,
        market: MarketSnapshotInput | None = None,
    ) -> ResearchMemo:
        ticker_u = ticker.strip().upper()
        market = market or MarketSnapshotInput()
        try:
            cik = self.sec.resolve_cik(ticker_u)
        except SecEdgarError:
            raise
        submissions = self.sec.get_submissions(cik)
        entity = str(submissions.get("name") or "") or None
        filings = self.sec.list_recent_filings(cik, forms=("10-K", "10-Q", "8-K"), limit=15)
        facts_raw = self.sec.get_company_facts(cik)
        financials = extract_company_facts(facts_raw)
        if financials.cik in {"", "0000000000"}:
            financials = financials.model_copy(update={"cik": str(cik).zfill(10)})
        dividends = extract_dividends_from_companyfacts(
            facts_raw,
            ticker=ticker_u,
            cik=cik,
            price=market.price,
            price_as_of=market.price_as_of,
        )
        screen = screen_issuer(
            ticker_u,
            strategy=self.config.strategy,
            dividends=dividends,
            market=market,
        )
        identity = IssuerIdentity(
            ticker=ticker_u,
            cik=str(cik).zfill(10),
            entity_name=entity or financials.entity_name,
            optionable=market.optionable,
        )
        memo = build_deterministic_memo(
            identity=identity,
            filings=filings,
            financials=financials,
            dividends=dividends,
            market=market,
            screen=screen,
            max_age_days=self.config.research.max_age_days_for_research_memo,
        )
        decision, extra = qualify_memo(memo, self.config.strategy)
        criteria = tuple(list(memo.criteria) + extra)
        memo = memo.model_copy(
            update={
                "decision": decision,
                "criteria": criteria,
                "decision_reasons": memo.decision_reasons
                if decision == memo.decision
                else tuple(
                    list(memo.decision_reasons) + [c.detail for c in extra if c.status != "pass"]
                ),
            }
        )
        return self.agent.enrich_memo(memo)
