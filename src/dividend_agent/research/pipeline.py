"""End-to-end issuer research pipeline (SEC + narrative + screen + memo). No trading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dividend_agent.agents.interface import ResearchAgent
from dividend_agent.agents.research_agent import DeterministicResearchAgent
from dividend_agent.agents.schema import ResearchBudget
from dividend_agent.config import AppConfig
from dividend_agent.research.citations import Citation
from dividend_agent.research.company_facts import extract_company_facts
from dividend_agent.research.dividends import extract_dividends_from_companyfacts
from dividend_agent.research.memo import IssuerIdentity, ResearchMemo, build_deterministic_memo
from dividend_agent.research.narrative import NarrativeAnalysis, analyze_filing_narrative
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
            cache_ttl_seconds=config.research.sec_cache_ttl_seconds,
        )
        return cls(
            config=config,
            sec=client,
            agent=agent or DeterministicResearchAgent(),
        )

    def _analyze_latest_narratives(self, filings: list[Citation]) -> NarrativeAnalysis | None:
        """Retrieve/analyze latest 10-K then subsequent 10-Q; filing link alone is insufficient."""
        ordered = sorted(
            [c for c in filings if c.form in {"10-K", "10-K/A", "10-Q", "10-Q/A"}],
            key=lambda c: c.filing_date or c.report_period or date.min,
            reverse=True,
        )
        # Prefer a 10-K narrative first
        ten_k = next((c for c in ordered if c.form and c.form.startswith("10-K")), None)
        ten_q = next((c for c in ordered if c.form and c.form.startswith("10-Q")), None)
        target = ten_k or ten_q
        if target is None:
            return None
        try:
            text = self.sec.fetch_filing_document(target)
        except SecEdgarError:
            # Missing narrative retrieval ⇒ explicit empty analysis (fail closed later).
            return analyze_filing_narrative("", citation=target)
        primary = analyze_filing_narrative(text, citation=target)
        # Optionally merge subsequent 10-Q evidence
        if ten_q is not None and ten_k is not None and ten_q.accession != ten_k.accession:
            try:
                q_text = self.sec.fetch_filing_document(ten_q)
                secondary = analyze_filing_narrative(q_text, citation=ten_q)
                # Keep primary substantive flag; note Q gaps in missing_sections
                missing = tuple(
                    dict.fromkeys(list(primary.missing_sections) + list(secondary.missing_sections))
                )
                return NarrativeAnalysis(
                    business=primary.business or secondary.business,
                    risks=primary.risks or secondary.risks,
                    debt=primary.debt or secondary.debt,
                    cash_flow=primary.cash_flow or secondary.cash_flow,
                    dividend_sustainability=(
                        primary.dividend_sustainability or secondary.dividend_sustainability
                    ),
                    missing_sections=missing,
                    source_accession=primary.source_accession,
                    has_substantive_evidence=primary.has_substantive_evidence
                    or secondary.has_substantive_evidence,
                )
            except SecEdgarError:
                return primary
        return primary

    def research_symbol(
        self,
        ticker: str,
        *,
        market: MarketSnapshotInput | None = None,
        filing_text_overrides: dict[str, str] | None = None,
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

        narrative: NarrativeAnalysis | None
        if filing_text_overrides:
            # Test hook: map accession -> text without HTTP
            from dividend_agent.research.narrative import analyze_filing_narrative as _an

            ten_k = next((c for c in filings if c.form and c.form.startswith("10-K")), None)
            if ten_k and ten_k.accession in filing_text_overrides:
                narrative = _an(filing_text_overrides[ten_k.accession], citation=ten_k)
            elif ten_k:
                narrative = _an("", citation=ten_k)
            else:
                narrative = None
        else:
            narrative = self._analyze_latest_narratives(filings)

        memo = build_deterministic_memo(
            identity=identity,
            filings=filings,
            financials=financials,
            dividends=dividends,
            market=market,
            screen=screen,
            max_age_days=self.config.research.max_age_days_for_research_memo,
        )

        # Attach narrative claims / unknowns — link alone must not qualify.
        extras = dict(memo.extras)
        claims = list(memo.claims)
        unknowns = list(memo.unknowns)
        risks = list(memo.risks)
        if narrative is None:
            unknowns.append("filing_narrative_unavailable")
        else:
            extras["narrative"] = {
                "has_substantive_evidence": narrative.has_substantive_evidence,
                "missing_sections": list(narrative.missing_sections),
                "source_accession": narrative.source_accession,
            }
            for claim in (
                narrative.business,
                narrative.risks,
                narrative.debt,
                narrative.cash_flow,
                narrative.dividend_sustainability,
            ):
                if claim is not None:
                    claims.append(claim)
            if narrative.risks is not None:
                risks.append(narrative.risks)
            if not narrative.has_substantive_evidence:
                unknowns.append("missing_filing_narrative_evidence")
            for section in narrative.missing_sections:
                unknowns.append(f"narrative_missing_section:{section}")

        # Source-date freshness: memo created_at must not make old companyfacts look fresh
        fact_dates = [f.filed for f in financials.facts.values() if f.filed is not None]
        if fact_dates:
            newest_fact = max(fact_dates)
            extras["newest_fact_filed"] = newest_fact.isoformat()
            age = (memo.created_at.date() - newest_fact).days
            extras["fact_source_age_days"] = age
            if age > self.config.research.max_age_days_for_research_memo:
                unknowns.append(f"companyfacts_stale_age_days={age}")

        memo = memo.model_copy(
            update={
                "claims": tuple(claims),
                "risks": tuple(risks),
                "unknowns": tuple(dict.fromkeys(unknowns)),
                "extras": extras,
            }
        )

        decision, extra = qualify_memo(
            memo,
            self.config.strategy,
            require_narrative=self.config.research.require_filing_narrative,
            narrative=narrative,
        )
        criteria = tuple(list(memo.criteria) + extra)
        memo = memo.model_copy(
            update={
                "decision": decision,
                "criteria": criteria,
                "decision_reasons": tuple(
                    dict.fromkeys(
                        list(memo.decision_reasons)
                        + [c.detail for c in extra if c.status != "pass"]
                    )
                ),
            }
        )

        excerpts: list[str] = []
        if narrative and narrative.business and narrative.business.citations:
            excerpts.extend(c.excerpt for c in narrative.business.citations if c.excerpt)
        budget = ResearchBudget(
            timeout_seconds=self.config.research.ai_research_timeout_seconds,
            max_tokens=self.config.research.ai_research_max_tokens,
        )
        _ = budget  # agent may use via stub factory; enrich_memo path sanitizes excerpts
        return self.agent.enrich_memo(memo, source_excerpts=excerpts)
