"""FastMCP server: chat agents trigger research/jobs via CLI only."""

from __future__ import annotations

import os
from typing import Any

from dividend_agent.mcp_server.allowlist import (
    ALLOWED_TOOL_NAMES,
    assert_allowlist_disjoint,
)
from dividend_agent.mcp_server.runner import (
    CliRunnerError,
    RunnerConfig,
    run_cli,
    validate_approval_id,
)

assert_allowlist_disjoint()

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - optional extra
    raise ImportError(
        "The MCP control plane requires the optional 'mcp' extra. "
        "Install with: uv sync --extra mcp   or   pip install 'dividend-agent[mcp]'"
    ) from exc


def create_server(
    *,
    runner_config: RunnerConfig | None = None,
    name: str = "dividend-agent-control-plane",
) -> FastMCP:
    """Build the MCP server with the hard-coded tool allowlist."""
    cfg = runner_config or RunnerConfig.from_env()
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8765"))
    instructions = (
        "Paper-only dividend-agent control plane. Tools wrap the dividend-agent "
        "CLI (local or docker exec). No Alpaca credentials, no raw order APIs, "
        "no free-text trade approval. Approvals require a concrete approval_id "
        "and never submit orders. Official Alpaca MCP belongs inside the broker "
        "adapter later — chat agents must not call it for trading."
    )
    mcp = FastMCP(name, instructions=instructions, host=host, port=port)

    def _run(cli_args: list[str]) -> str:
        try:
            result = run_cli(cli_args, cfg)
        except CliRunnerError as exc:
            return f"error: {exc}"
        return result.as_text()

    @mcp.tool(name="health", description="Process health (no broker call).")
    def health(config_path: str | None = None) -> str:
        args = ["health"]
        path = config_path or cfg.config_path
        if path:
            args.extend(["--config", path])
        return _run(args)

    @mcp.tool(
        name="config_validate",
        description="Validate strategy YAML schema and paper-only constraints.",
    )
    def config_validate(
        config_path: str | None = None,
        check_auto_submit: bool = False,
    ) -> str:
        path = config_path or cfg.config_path
        args = ["config", "validate", path]
        if check_auto_submit:
            args.append("--check-auto-submit")
        return _run(args)

    @mcp.tool(
        name="broker_verify_paper",
        description="Positively verify Alpaca paper account; fail closed if unverified.",
    )
    def broker_verify_paper() -> str:
        return _run(["broker", "verify-paper"])

    @mcp.tool(
        name="broker_reconcile",
        description="Pull paper ledger into SQLite. Never submits orders.",
    )
    def broker_reconcile(
        config_path: str | None = None,
        db_path: str | None = None,
    ) -> str:
        args = ["broker", "reconcile"]
        args.extend(["--config", config_path or cfg.config_path])
        args.extend(["--db", db_path or cfg.db_path])
        return _run(args)

    @mcp.tool(
        name="research_screen",
        description="Deterministic issuer screen gates (read-only).",
    )
    def research_screen(
        ticker: str,
        config_path: str | None = None,
        price: float | None = None,
        week52_high: float | None = None,
        week52_low: float | None = None,
        avg_dollar_volume: float | None = None,
        optionable: bool | None = None,
        trailing_yield: float | None = None,
    ) -> str:
        args = ["research", "screen", ticker.strip().upper()]
        args.extend(["--config", config_path or cfg.config_path])
        if price is not None:
            args.extend(["--price", str(price)])
        if week52_high is not None:
            args.extend(["--week52-high", str(week52_high)])
        if week52_low is not None:
            args.extend(["--week52-low", str(week52_low)])
        if avg_dollar_volume is not None:
            args.extend(["--avg-dollar-volume", str(avg_dollar_volume)])
        if optionable is not None:
            args.append("--optionable" if optionable else "--no-optionable")
        if trailing_yield is not None:
            args.extend(["--trailing-yield", str(trailing_yield)])
        return _run(args)

    @mcp.tool(
        name="research_memo",
        description="Build a cited SEC research memo for a ticker (research symbol).",
    )
    def research_memo(
        ticker: str,
        config_path: str | None = None,
        db_path: str | None = None,
        price: float | None = None,
        week52_high: float | None = None,
        week52_low: float | None = None,
        avg_dollar_volume: float | None = None,
        optionable: bool | None = None,
    ) -> str:
        args = ["research", "symbol", ticker.strip().upper()]
        args.extend(["--config", config_path or cfg.config_path])
        if db_path or cfg.db_path:
            args.extend(["--db", db_path or cfg.db_path])
        if price is not None:
            args.extend(["--price", str(price)])
        if week52_high is not None:
            args.extend(["--week52-high", str(week52_high)])
        if week52_low is not None:
            args.extend(["--week52-low", str(week52_low)])
        if avg_dollar_volume is not None:
            args.extend(["--avg-dollar-volume", str(avg_dollar_volume)])
        if optionable is not None:
            args.append("--optionable" if optionable else "--no-optionable")
        return _run(args)

    @mcp.tool(
        name="options_evaluate",
        description=(
            "Evaluate covered-call candidates from a chain JSON path. "
            "Proposes TradeIntents only — never submits orders."
        ),
    )
    def options_evaluate(
        chain_path: str,
        config_path: str | None = None,
        shares: int = 100,
        wac: float = 40.0,
        reserved_calls: int = 0,
        as_of: str | None = None,
        ex_date: str | None = None,
        set_liquidity_guardrails: bool = False,
    ) -> str:
        args = ["options", "evaluate", chain_path]
        args.extend(["--config", config_path or cfg.config_path])
        args.extend(["--shares", str(shares), "--wac", str(wac)])
        args.extend(["--reserved-calls", str(reserved_calls)])
        if as_of:
            args.extend(["--as-of", as_of])
        if ex_date:
            args.extend(["--ex-date", ex_date])
        if set_liquidity_guardrails:
            args.append("--set-liquidity-guardrails")
        else:
            args.append("--no-set-liquidity-guardrails")
        return _run(args)

    @mcp.tool(
        name="scheduler_run",
        description="Run scheduler jobs once (startup/trading_day/monthly/event_review).",
    )
    def scheduler_run(
        jobs: str = "startup,trading_day",
        config_path: str | None = None,
        db_path: str | None = None,
        reports_dir: str | None = None,
        force: bool = False,
        period: str | None = None,
        event: str | None = None,
        chain_path: str | None = None,
    ) -> str:
        args = ["scheduler", "run", "--jobs", jobs]
        args.extend(["--config", config_path or cfg.config_path])
        args.extend(["--db", db_path or cfg.db_path])
        args.extend(["--reports", reports_dir or cfg.reports_dir])
        if force:
            args.append("--force")
        if period:
            args.extend(["--period", period])
        if event:
            args.extend(["--event", event])
        if chain_path:
            args.extend(["--chain", chain_path])
        return _run(args)

    @mcp.tool(
        name="scheduler_status",
        description="Show recent scheduler job_keys and notifications.",
    )
    def scheduler_status(db_path: str | None = None) -> str:
        return _run(["scheduler", "status", "--db", db_path or cfg.db_path])

    @mcp.tool(
        name="approvals_list",
        description="List pending approval queue rows.",
    )
    def approvals_list(db_path: str | None = None) -> str:
        return _run(["approvals", "list", "--db", db_path or cfg.db_path])

    @mcp.tool(
        name="approvals_approve",
        description=(
            "Approve a pending exception by concrete approval_id only. "
            "Always uses --no-submit (never places orders via MCP)."
        ),
    )
    def approvals_approve(
        approval_id: str,
        config_path: str | None = None,
        db_path: str | None = None,
    ) -> str:
        try:
            aid = validate_approval_id(approval_id)
        except CliRunnerError as exc:
            return f"error: {exc}"
        args = [
            "approvals",
            "approve",
            aid,
            "--config",
            config_path or cfg.config_path,
            "--db",
            db_path or cfg.db_path,
            "--no-submit",
        ]
        return _run(args)

    @mcp.tool(
        name="approvals_reject",
        description="Reject a pending approval by concrete approval_id only.",
    )
    def approvals_reject(approval_id: str, db_path: str | None = None) -> str:
        try:
            aid = validate_approval_id(approval_id)
        except CliRunnerError as exc:
            return f"error: {exc}"
        return _run(["approvals", "reject", aid, "--db", db_path or cfg.db_path])

    @mcp.tool(
        name="status_summary",
        description="Read-only health + pending approvals + recent report files.",
    )
    def status_summary(
        config_path: str | None = None,
        db_path: str | None = None,
        reports_dir: str | None = None,
    ) -> str:
        args = ["reports", "summary"]
        args.extend(["--config", config_path or cfg.config_path])
        args.extend(["--db", db_path or cfg.db_path])
        args.extend(["--reports", reports_dir or cfg.reports_dir])
        return _run(args)

    # Bind tools into closure for introspection helpers without unused warnings.
    _ = (
        health,
        config_validate,
        broker_verify_paper,
        broker_reconcile,
        research_screen,
        research_memo,
        options_evaluate,
        scheduler_run,
        scheduler_status,
        approvals_list,
        approvals_approve,
        approvals_reject,
        status_summary,
    )
    return mcp


async def registered_tool_names(server: FastMCP | None = None) -> frozenset[str]:
    """Return tool names registered on a server (or a fresh default server)."""
    mcp = server or create_server()
    tools = await mcp.list_tools()
    return frozenset(t.name for t in tools)


def assert_registered_tools_allowed(names: frozenset[str]) -> None:
    """Fail closed if registration drifts from the allowlist."""
    unexpected = names - ALLOWED_TOOL_NAMES
    missing = ALLOWED_TOOL_NAMES - names
    if unexpected or missing:
        raise RuntimeError(
            "MCP tool registration mismatch: "
            f"unexpected={sorted(unexpected)} missing={sorted(missing)}"
        )


def run_server(
    *,
    transport: str | None = None,
    runner_config: RunnerConfig | None = None,
) -> None:
    """Entrypoint used by ``python -m dividend_agent.mcp_server``."""
    mcp = create_server(runner_config=runner_config)
    chosen = (transport or os.environ.get("MCP_TRANSPORT") or "stdio").strip().lower()
    if chosen not in {"stdio", "sse", "streamable-http"}:
        raise SystemExit(f"unsupported MCP_TRANSPORT={chosen!r}; use stdio|sse|streamable-http")
    # Optional bearer gate for HTTP transports (LAN). Stdio has no HTTP surface.
    if chosen in {"sse", "streamable-http"}:
        _install_http_auth_gate(mcp)
    mcp.run(transport=chosen)  # type: ignore[arg-type]


def _install_http_auth_gate(mcp: FastMCP) -> None:
    """Fail closed for non-loopback HTTP unless MCP_AUTH_TOKEN is set.

    Primary control: bind ``MCP_HOST=127.0.0.1``. For intentional LAN exposure,
    set a non-empty ``MCP_AUTH_TOKEN`` and put a reverse proxy / firewall in
    front; clients must send ``Authorization: Bearer <token>``. Stdio has no
    HTTP surface and skips this gate.
    """
    token = (os.environ.get("MCP_AUTH_TOKEN") or "").strip()
    host = (os.environ.get("MCP_HOST") or mcp.settings.host or "127.0.0.1").strip()
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if not token and not loopback:
        raise SystemExit(
            "MCP_HOST is not loopback and MCP_AUTH_TOKEN is empty — refusing to start. "
            "Bind to 127.0.0.1 or set MCP_AUTH_TOKEN for LAN access."
        )
    if not token:
        return

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Any) -> Response:
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)  # type: ignore[no-any-return]

    def _wrap(factory: Any) -> Any:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            app = factory(*args, **kwargs)
            app.add_middleware(BearerAuthMiddleware)
            return app

        return wrapped

    mcp.sse_app = _wrap(mcp.sse_app)  # type: ignore[method-assign]
    mcp.streamable_http_app = _wrap(mcp.streamable_http_app)  # type: ignore[method-assign]
