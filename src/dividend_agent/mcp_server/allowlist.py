"""Hard allowlist / denylist for the chat MCP control plane.

Chat agents may only invoke these tools. Each tool shells into the
``dividend-agent`` CLI (local binary or ``docker exec``). No Alpaca MCP,
no raw order APIs, and no free-text trade approval.
"""

from __future__ import annotations

from typing import Final

# Canonical MCP tool names exposed to Grok / Claude / Cursor.
ALLOWED_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "health",
        "config_validate",
        "broker_verify_paper",
        "broker_reconcile",
        "research_screen",
        "research_memo",
        "options_evaluate",
        "scheduler_run",
        "scheduler_status",
        "approvals_list",
        "approvals_approve",
        "approvals_reject",
        "status_summary",
    }
)

# Names that must never appear as MCP tools (regression guard).
FORBIDDEN_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "submit_order",
        "submit_paper_order",
        "place_order",
        "trade_submit",
        "trade_place",
        "buy",
        "sell",
        "approve_trade",
        "approve_this_trade",
        "free_text_approve",
        "alpaca_submit",
        "alpaca_mcp",
        "live_trade",
        "ops_record_promote",
        "ops_emergency_stop",
        "trade_recover",
        "trade_status",
    }
)

# CLI argv prefixes permitted by the runner (positional command path only).
ALLOWED_CLI_PREFIXES: Final[tuple[tuple[str, ...], ...]] = (
    ("health",),
    ("config", "validate"),
    ("broker", "verify-paper"),
    ("broker", "reconcile"),
    ("broker", "status"),
    ("research", "screen"),
    ("research", "symbol"),
    ("options", "evaluate"),
    ("scheduler", "run"),
    ("scheduler", "status"),
    ("approvals", "list"),
    ("approvals", "approve"),
    ("approvals", "reject"),
    ("reports", "summary"),
)

# Tokens that must never appear in CLI argv constructed for MCP tools.
FORBIDDEN_CLI_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "--submit",
        "trade",
        "submit",
        "live",
        "ALPACA_API_KEY",
        "ALPACA_API_SECRET",
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_API_SECRET",
        "ALPACA_LIVE_API_KEY",
        "ALPACA_LIVE_API_SECRET",
    }
)

# Environment variable names never forwarded into docker-exec argv / never
# returned in tool payloads. Local backend inherits the operator process env
# for CLI needs, but MCP HTTP responses must not echo these values.
SECRET_ENV_NAMES: Final[frozenset[str]] = frozenset(
    {
        "ALPACA_API_KEY",
        "ALPACA_API_SECRET",
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_API_SECRET",
        "ALPACA_LIVE_API_KEY",
        "ALPACA_LIVE_API_SECRET",
        "ALPACA_SECRET_KEY",
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
    }
)


def allowed_tool_names() -> frozenset[str]:
    """Return the frozen allowlist (test / introspection helper)."""
    return ALLOWED_TOOL_NAMES


def assert_allowlist_disjoint() -> None:
    """Fail closed if a forbidden name is accidentally allowlisted."""
    overlap = ALLOWED_TOOL_NAMES & FORBIDDEN_TOOL_NAMES
    if overlap:
        raise RuntimeError(f"MCP allowlist intersects denylist: {sorted(overlap)}")
