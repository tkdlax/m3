"""In-process order authority boundary (Milestone A).

The agent / research path must not hold brokerage write credentials or call
broker submit APIs directly. All paper submits go through ``OrderManager``
(the gateway). This module documents and enforces that seam as far as a single
process allows:

- ``AGENT_FORBIDDEN_BROKER_WRITES`` — capability names the agent may not invoke.
- ``assert_agent_has_no_write_credentials`` — refuse agent overlays that include
  Alpaca paper secrets.
- ``decision_client_order_id`` — stable idempotency across regenerated intent UUIDs.

Full process isolation (separate gateway container / MCP control plane without
secrets) is complementary; see open PR #10 and ``docs/MCP_CONTROL_PLANE.md``
when merged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from dividend_agent.domain.models import TradeIntent
from dividend_agent.execution.coverage import underlying_from_intent

AGENT_FORBIDDEN_BROKER_WRITES: frozenset[str] = frozenset(
    {
        "submit_paper_order",
        "cancel_paper_order",
    }
)

_WRITE_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_API_SECRET",
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
    }
)


class OrderAuthorityError(RuntimeError):
    """Agent attempted to use gateway-only write authority."""


def assert_agent_has_no_write_credentials(environ: Mapping[str, str]) -> None:
    """Fail if an agent-scoped environ overlay contains brokerage write secrets."""
    present = sorted(k for k in _WRITE_SECRET_KEYS if (environ.get(k) or "").strip())
    if present:
        raise OrderAuthorityError(
            "agent environ must not include brokerage write credentials: " + ", ".join(present)
        )


def assert_capabilities_are_read_only(capabilities: tuple[str, ...] | list[str]) -> None:
    """Fail if a capability set includes broker write operations."""
    banned = sorted(set(capabilities) & AGENT_FORBIDDEN_BROKER_WRITES)
    if banned:
        raise OrderAuthorityError(
            "agent capability allowlist must not include write tools: " + ", ".join(banned)
        )


def decision_fingerprint(intent: TradeIntent) -> str:
    """Stable logical decision identity (not the intent UUID)."""
    underlying = underlying_from_intent(intent) or ""
    expiry = ""
    if intent.expiry is not None:
        expiry = intent.expiry.astimezone().date().isoformat()
    parts = [
        intent.account_id,
        underlying,
        intent.symbol,
        intent.side.lower(),
        str(intent.quantity),
        intent.order_type.lower(),
        str(intent.strike) if intent.strike is not None else "",
        expiry,
        str(intent.deliverable_shares) if intent.deliverable_shares is not None else "",
        intent.rule_config_version,
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def decision_client_order_id(intent: TradeIntent) -> str:
    """Idempotency key stable across regenerated proposals for the same decision."""
    return f"da-{decision_fingerprint(intent)}"
