"""Research and screening modules (M2).

``ResearchPipeline`` is loaded lazily so ``agents.schema`` can import citations
without a circular import through ``pipeline`` → ``agents.interface``.
"""

from dividend_agent.research.memo import ResearchMemo
from dividend_agent.research.screener import MarketSnapshotInput, ScreenResult, screen_issuer

__all__ = [
    "MarketSnapshotInput",
    "ResearchMemo",
    "ResearchPipeline",
    "ScreenResult",
    "screen_issuer",
]


def __getattr__(name: str) -> object:
    if name == "ResearchPipeline":
        from dividend_agent.research.pipeline import ResearchPipeline

        return ResearchPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
