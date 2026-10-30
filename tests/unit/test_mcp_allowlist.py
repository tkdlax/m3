"""MCP control-plane allowlist: forbidden order tools must stay absent."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from dividend_agent.mcp_server.allowlist import (
    ALLOWED_TOOL_NAMES,
    FORBIDDEN_CLI_TOKENS,
    FORBIDDEN_TOOL_NAMES,
    assert_allowlist_disjoint,
)
from dividend_agent.mcp_server.runner import (
    CliBackend,
    CliRunnerError,
    RunnerConfig,
    build_process_argv,
    run_cli,
    validate_approval_id,
    validate_cli_argv,
)

pytest.importorskip("mcp")

from dividend_agent.mcp_server.server import (  # noqa: E402
    assert_registered_tools_allowed,
    create_server,
    registered_tool_names,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "config" / "strategy.example.yaml"


def test_allowlist_disjoint_from_denylist() -> None:
    assert_allowlist_disjoint()
    assert ALLOWED_TOOL_NAMES.isdisjoint(FORBIDDEN_TOOL_NAMES)


def test_forbidden_order_tools_not_allowlisted() -> None:
    for name in (
        "submit_order",
        "submit_paper_order",
        "place_order",
        "trade_submit",
        "approve_trade",
        "alpaca_mcp",
        "live_trade",
    ):
        assert name in FORBIDDEN_TOOL_NAMES
        assert name not in ALLOWED_TOOL_NAMES


def test_registered_tools_match_allowlist_exactly() -> None:
    names = asyncio.run(registered_tool_names())
    assert names == ALLOWED_TOOL_NAMES
    assert_registered_tools_allowed(names)
    for banned in FORBIDDEN_TOOL_NAMES:
        assert banned not in names


def test_validate_cli_rejects_submit_and_trade() -> None:
    with pytest.raises(CliRunnerError, match="allowlist|forbidden"):
        validate_cli_argv(["trade", "status"])
    with pytest.raises(CliRunnerError, match="submit|forbidden"):
        validate_cli_argv(
            ["approvals", "approve", "abc", "--submit"],
        )
    assert "--submit" in FORBIDDEN_CLI_TOKENS


def test_validate_cli_allows_health_and_approve_no_submit() -> None:
    validate_cli_argv(["health"])
    validate_cli_argv(
        ["approvals", "approve", "appr-1", "--config", "c.yaml", "--db", "db", "--no-submit"]
    )


def test_approval_id_rejects_free_text() -> None:
    with pytest.raises(CliRunnerError, match="free-text|approval_id"):
        validate_approval_id("please approve this GIS covered call trade")
    assert validate_approval_id("appr-01") == "appr-01"


def test_docker_backend_argv_has_no_alpaca_env_flags() -> None:
    cfg = RunnerConfig(backend=CliBackend.DOCKER, container="dividend-agent")
    argv = build_process_argv(["broker", "verify-paper"], cfg)
    assert argv == [
        "docker",
        "exec",
        "dividend-agent",
        "dividend-agent",
        "broker",
        "verify-paper",
    ]
    joined = " ".join(argv)
    assert "ALPACA" not in joined
    assert "-e" not in argv


def test_run_cli_health_local() -> None:
    cfg = RunnerConfig(
        backend=CliBackend.LOCAL,
        cli_bin="dividend-agent",
        config_path=str(EXAMPLE),
    )
    # Ensure console script is on PATH via uv-run environment when tests execute.
    result = run_cli(["health", "--config", str(EXAMPLE)], cfg)
    assert result.exit_code == 0
    assert "live_trading" in result.stdout or "ok" in result.stdout.lower() or "{" in result.stdout


def test_approvals_approve_tool_forces_no_submit() -> None:
    server = create_server(
        runner_config=RunnerConfig(backend=CliBackend.LOCAL, config_path=str(EXAMPLE))
    )
    captured: list[list[str]] = []

    def fake_run(cli_args: list[str], cfg: RunnerConfig | None = None):  # noqa: ANN001
        from dividend_agent.mcp_server.runner import CliResult

        captured.append(list(cli_args))
        return CliResult(argv=tuple(cli_args), exit_code=0, stdout="{}", stderr="")

    with patch("dividend_agent.mcp_server.server.run_cli", side_effect=fake_run):
        result = asyncio.run(server.call_tool("approvals_approve", {"approval_id": "approval-123"}))
    assert captured
    assert captured[0][:3] == ["approvals", "approve", "approval-123"]
    assert "--no-submit" in captured[0]
    assert "--submit" not in captured[0]
    assert "--submit" not in str(result)


def test_status_summary_tool_uses_reports_summary() -> None:
    server = create_server(
        runner_config=RunnerConfig(backend=CliBackend.LOCAL, config_path=str(EXAMPLE))
    )
    captured: list[list[str]] = []

    def fake_run(cli_args: list[str], cfg: RunnerConfig | None = None):  # noqa: ANN001
        from dividend_agent.mcp_server.runner import CliResult

        captured.append(list(cli_args))
        return CliResult(argv=tuple(cli_args), exit_code=0, stdout="{}", stderr="")

    with patch("dividend_agent.mcp_server.server.run_cli", side_effect=fake_run):
        asyncio.run(server.call_tool("status_summary", {}))
    assert captured[0][:2] == ["reports", "summary"]


def test_http_non_loopback_without_token_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    from dividend_agent.mcp_server import server as server_mod

    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    mcp = create_server()
    with pytest.raises(SystemExit, match="MCP_AUTH_TOKEN|loopback"):
        server_mod._install_http_auth_gate(mcp)


def test_runner_from_env_docker_strips_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIVIDEND_AGENT_MCP_BACKEND", "docker")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "paper-key-should-not-leak")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "paper-secret-should-not-leak")
    cfg = RunnerConfig.from_env()
    assert cfg.backend is CliBackend.DOCKER
    with patch("subprocess.run") as mocked:
        mocked.return_value = type(
            "R",
            (),
            {"returncode": 0, "stdout": "ok", "stderr": ""},
        )()
        run_cli(["health"], cfg)
        env = mocked.call_args.kwargs["env"]
        assert "ALPACA_PAPER_API_KEY" not in env
        assert "ALPACA_PAPER_API_SECRET" not in env


def test_reports_summary_cli() -> None:
    from typer.testing import CliRunner

    from dividend_agent.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "reports",
            "summary",
            "--config",
            str(EXAMPLE),
            "--db",
            str(ROOT / "data" / "missing.sqlite3"),
            "--reports",
            str(ROOT / "docs"),
        ],
    )
    assert result.exit_code == 0
    assert "pending_approvals" in result.stdout
    assert "submission" in result.stdout
