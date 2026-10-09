"""Stats tools in-process and through the MCP server over stdio."""

import asyncio
from pathlib import Path

import pytest

from pitch_ledger.cli import main
from pitch_ledger.engine import MatchEngine
from pitch_ledger.mcp_server.client import LocalStats, McpStats
from pitch_ledger.mcp_server.tools import StatsTools


@pytest.fixture(scope="module")
def match_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("data") / "M0002"
    assert main(["generate", "--scenario", "late_collapse", "--seed", "4212",
                 "--out", str(out)]) == 0  # fmt: skip
    main(["detect", str(out)])
    return out


@pytest.fixture(scope="module")
def tools(match_dir: Path) -> StatsTools:
    return StatsTools(match_dir)


def test_every_trigger_recomputes_through_the_tools(tools: StatsTools) -> None:
    for trigger_id, trigger in tools.triggers.items():
        assert tools.recompute_trigger(trigger_id) == trigger.metrics, trigger.rule_id


def test_recompute_metric_reports_match(tools: StatsTools) -> None:
    momentum = next(t for t in tools.triggers.values() if t.rule_id == "momentum_shift")
    out = tools.recompute_metric(momentum.trigger_id, "turnovers_after")
    assert out["matches"] and out["value"] == momentum.metrics["turnovers_after"]


def test_team_state_equals_engine(tools: StatsTools) -> None:
    engine = MatchEngine(tools.engine.match, tools.events)
    state = tools.get_team_state("T02", 2, 15 * 60_000, 25 * 60_000)
    window = engine.team_window("T02", 2, 15 * 60_000, 25 * 60_000)
    assert state["control"] == window.control and state["turnovers"] == window.turnovers
    with pytest.raises(ValueError):
        tools.get_team_state("T05", 2, 0, 60_000)


def test_player_stats_and_events(tools: StatsTools) -> None:
    stats = tools.get_player_stats("T01-P09")
    assert stats["passes"] >= stats["passes_completed"] >= 0
    assert stats["distance_km"] and stats["distance_km"] > 5
    ids = [tools.events[3].event_id, tools.events[7].event_id]
    assert [e["event_id"] for e in tools.get_events(ids)] == ids
    assert len(tools.get_events(period=1, start_ms=0, end_ms=60_000)) > 0


def test_mcp_stdio_round_trip(match_dir: Path, tools: StatsTools) -> None:
    momentum = next(t for t in tools.triggers.values() if t.rule_id == "momentum_shift")

    async def run() -> tuple[dict[str, int | float], list[dict[str, object]]]:
        async with McpStats(match_dir) as stats:
            fresh = await stats.recompute_trigger(momentum.trigger_id)
            rows = await stats.get_events(momentum.evidence_event_ids[:3])
            return fresh, rows

    fresh, rows = asyncio.run(run())
    assert fresh == momentum.metrics
    assert [r["event_id"] for r in rows] == momentum.evidence_event_ids[:3]


def test_local_gateway(tools: StatsTools) -> None:
    trigger = next(iter(tools.triggers.values()))
    assert asyncio.run(LocalStats(tools).recompute_trigger(trigger.trigger_id)) == trigger.metrics
