"""Execute allowlisted ``dividend-agent`` CLI invocations.

Backends:
- ``local``: run ``dividend-agent`` on PATH (dev / Cursor stdio on the host).
- ``docker``: ``docker exec <container> dividend-agent …`` (TrueNAS sidecar).

Alpaca credentials stay inside the agent process/container. This runner never
embeds ``ALPACA_*`` values into argv, never exposes them in results, and never
accepts ``--submit`` on approvals.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from dividend_agent.mcp_server.allowlist import (
    ALLOWED_CLI_PREFIXES,
    FORBIDDEN_CLI_TOKENS,
    SECRET_ENV_NAMES,
)

_APPROVAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+=:@%()-]*$")


class CliBackend(StrEnum):
    LOCAL = "local"
    DOCKER = "docker"


class CliRunnerError(RuntimeError):
    """Raised when argv fails allowlist checks or the process cannot start."""


@dataclass(frozen=True)
class CliResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str

    def as_text(self) -> str:
        parts = [
            f"exit_code={self.exit_code}",
            f"argv={shlex.join(self.argv)}",
        ]
        if self.stdout.strip():
            parts.append("--- stdout ---\n" + self.stdout.rstrip())
        if self.stderr.strip():
            parts.append("--- stderr ---\n" + _redact_secrets(self.stderr).rstrip())
        return "\n".join(parts)


@dataclass(frozen=True)
class RunnerConfig:
    backend: CliBackend = CliBackend.LOCAL
    cli_bin: str = "dividend-agent"
    docker_bin: str = "docker"
    container: str = "dividend-agent"
    config_path: str = "/config/strategy.yaml"
    db_path: str = "/data/agent.sqlite3"
    reports_dir: str = "/reports"
    timeout_sec: float = 180.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RunnerConfig:
        e = os.environ if env is None else env
        backend_raw = (e.get("DIVIDEND_AGENT_MCP_BACKEND") or "local").strip().lower()
        try:
            backend = CliBackend(backend_raw)
        except ValueError as exc:
            raise CliRunnerError(
                f"invalid DIVIDEND_AGENT_MCP_BACKEND={backend_raw!r}; expected local|docker"
            ) from exc
        timeout_raw = e.get("DIVIDEND_AGENT_MCP_TIMEOUT_SEC", "180")
        try:
            timeout = float(timeout_raw)
        except ValueError as exc:
            raise CliRunnerError(f"invalid DIVIDEND_AGENT_MCP_TIMEOUT_SEC={timeout_raw!r}") from exc
        return cls(
            backend=backend,
            cli_bin=e.get("DIVIDEND_AGENT_CLI_BIN", "dividend-agent"),
            docker_bin=e.get("DIVIDEND_AGENT_DOCKER_BIN", "docker"),
            container=e.get("DIVIDEND_AGENT_MCP_CONTAINER", "dividend-agent"),
            config_path=e.get("DIVIDEND_AGENT_CONFIG_PATH", "/config/strategy.yaml"),
            db_path=e.get("DIVIDEND_AGENT_DB_PATH", "/data/agent.sqlite3"),
            reports_dir=e.get("DIVIDEND_AGENT_REPORTS_DIR", "/reports"),
            timeout_sec=timeout,
        )


def validate_approval_id(approval_id: str) -> str:
    value = approval_id.strip()
    if not _APPROVAL_ID_RE.fullmatch(value):
        raise CliRunnerError(
            "approval_id must be a concrete queue id "
            "(alphanumeric / . _ : -); free-text trade approvals are refused"
        )
    return value


def validate_cli_argv(argv: list[str]) -> None:
    """Fail closed unless argv matches an allowlisted CLI prefix."""
    if not argv:
        raise CliRunnerError("empty CLI argv")
    for token in argv:
        if token in FORBIDDEN_CLI_TOKENS:
            raise CliRunnerError(f"forbidden CLI token refused: {token}")
        if token.startswith("--submit"):
            raise CliRunnerError("order submit flags are refused on the MCP plane")
        lower = token.lower()
        if "alpaca" in lower and ("key" in lower or "secret" in lower):
            raise CliRunnerError("Alpaca credential tokens refused in CLI argv")
    matched = False
    for prefix in ALLOWED_CLI_PREFIXES:
        if tuple(argv[: len(prefix)]) == prefix:
            matched = True
            break
    if not matched:
        raise CliRunnerError(f"CLI argv not on MCP allowlist: {argv!r}")


def build_process_argv(cli_args: list[str], cfg: RunnerConfig) -> list[str]:
    validate_cli_argv(cli_args)
    if cfg.backend is CliBackend.LOCAL:
        return [cfg.cli_bin, *cli_args]
    # docker exec — do not pass -e ALPACA_*; credentials stay in the agent container.
    if not _SAFE_TOKEN_RE.fullmatch(cfg.container):
        raise CliRunnerError(f"unsafe container name: {cfg.container!r}")
    return [cfg.docker_bin, "exec", cfg.container, cfg.cli_bin, *cli_args]


def run_cli(cli_args: list[str], cfg: RunnerConfig | None = None) -> CliResult:
    config = cfg or RunnerConfig.from_env()
    proc_argv = build_process_argv(cli_args, config)
    env = _subprocess_env(config)
    try:
        completed = subprocess.run(  # noqa: S603 — argv allowlisted above
            proc_argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=config.timeout_sec,
            env=env,
        )
    except FileNotFoundError as exc:
        raise CliRunnerError(f"executable not found: {proc_argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CliRunnerError(
            f"CLI timed out after {config.timeout_sec}s: {shlex.join(proc_argv)}"
        ) from exc
    return CliResult(
        argv=tuple(proc_argv),
        exit_code=completed.returncode,
        stdout=_redact_secrets(completed.stdout or ""),
        stderr=_redact_secrets(completed.stderr or ""),
    )


def _subprocess_env(cfg: RunnerConfig) -> dict[str, str]:
    """Build env for the MCP→CLI subprocess.

    Docker backend: strip Alpaca secrets from the *mcp* process env so a
    misconfigured sidecar cannot leak keys into ``docker exec`` inheritance.
    Local backend: inherit operator env (CLI may need paper keys) but never
    put secrets into argv or tool return payloads.
    """
    base = dict(os.environ)
    if cfg.backend is CliBackend.DOCKER:
        for name in SECRET_ENV_NAMES:
            base.pop(name, None)
        # Also strip any ALPACA_* / APCA_* the operator might have set on the MCP service.
        for key in list(base):
            if key.startswith(("ALPACA_", "APCA_")):
                base.pop(key, None)
    return base


def _redact_secrets(text: str) -> str:
    redacted = text
    for name in sorted(SECRET_ENV_NAMES):
        value = os.environ.get(name)
        if value and len(value) >= 4:
            redacted = redacted.replace(value, f"[REDACTED:{name}]")
    # Generic key=value patterns that look like Alpaca secret dumps.
    redacted = re.sub(
        r"(?i)(ALPACA_[A-Z0-9_]*SECRET[A-Z0-9_]*\s*[=:]\s*)(\S+)",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(ALPACA_[A-Z0-9_]*KEY[A-Z0-9_]*\s*[=:]\s*)(\S+)",
        r"\1[REDACTED]",
        redacted,
    )
    return redacted
