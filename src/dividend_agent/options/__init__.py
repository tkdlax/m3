"""Options chain, probability, and covered-call gates (M3)."""

from dividend_agent.options.chains import OptionChain, load_chain_json, parse_chain_payload
from dividend_agent.options.gates import CandidateEvaluation, evaluate_covered_call_candidate
from dividend_agent.options.probability import (
    ProbabilityResult,
    compute_short_call_probabilities,
    norm_cdf,
)

__all__ = [
    "CandidateEvaluation",
    "OptionChain",
    "ProbabilityResult",
    "compute_short_call_probabilities",
    "evaluate_covered_call_candidate",
    "load_chain_json",
    "norm_cdf",
    "parse_chain_payload",
]
