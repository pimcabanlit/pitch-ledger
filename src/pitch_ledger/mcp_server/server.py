"""MCP server exposing the read-only stats tools over stdio.

Run: ``pitch-ledger mcp data/M0002`` (or ``python -m pitch_ledger.mcp_server data/M0002``).
"""

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from pitch_ledger.mcp_server.tools import StatsTools


def build_server(match_dir: Path | str) -> FastMCP:
    tools = StatsTools(match_dir)
    server = FastMCP("pitch-ledger-stats", log_level="WARNING")

    @server.tool()
    def get_events(
        event_ids: list[str] | None = None,
        period: int | None = None,
        start_ms: int = -1,
        end_ms: int = 10**9,
    ) -> list[dict[str, Any]]:
        """Event rows by ID, or within (start_ms, end_ms] of a period (max 300)."""
        return tools.get_events(event_ids, period, start_ms, end_ms)

    @server.tool()
    def get_team_state(team_id: str, period: int, start_ms: int, end_ms: int) -> dict[str, Any]:
        """Possession share, passes, accuracy, turnovers, PPDA, tempo and control score."""
        return tools.get_team_state(team_id, period, start_ms, end_ms)

    @server.tool()
    def get_player_stats(
        player_id: str, period: int | None = None, start_ms: int = -1, end_ms: int = 10**9
    ) -> dict[str, Any]:
        """A player's passing, shooting, turnovers and running."""
        return tools.get_player_stats(player_id, period, start_ms, end_ms)

    @server.tool()
    def get_trigger(trigger_id: str) -> dict[str, Any]:
        """A detected moment: metrics, evidence event IDs and windows."""
        return tools.get_trigger(trigger_id)

    @server.tool()
    def recompute_trigger(trigger_id: str) -> dict[str, Any]:
        """Every metric of a trigger recomputed from raw rows up to its moment."""
        return dict(tools.recompute_trigger(trigger_id))

    @server.tool()
    def recompute_metric(trigger_id: str, metric: str) -> dict[str, Any]:
        """One metric recomputed, next to the value the trigger states."""
        return tools.recompute_metric(trigger_id, metric)

    return server


def serve(match_dir: Path | str) -> None:
    """Serve the tools for ``match_dir`` over stdio until the client disconnects."""
    build_server(match_dir).run(transport="stdio")
