"""Console entry: ``python -m dividend_agent.mcp_server`` / ``dividend-agent-mcp``."""

from __future__ import annotations

from dividend_agent.mcp_server.server import run_server


def main() -> None:
    run_server()


if __name__ == "__main__":
    main()
