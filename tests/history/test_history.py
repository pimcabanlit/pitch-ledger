"""Match records: built from the rows with the engine's definitions, kept per season."""

from pathlib import Path
from typing import Any

import pytest

from pitch_ledger.engine import rule_metrics as rm
from pitch_ledger.engine.windows import MatchEngine
from pitch_ledger.history import (
    FileHistory,
    InMemoryHistory,
    MatchNotFinished,
    build_record,
    season_for,
)
from pitch_ledger.history.table import league_table
from pitch_ledger.infra_adapters.cosmos_history import CosmosHistory
from pitch_ledger.mcp_server.tools import StatsTools
from pitch_ledger.schema.enums import EventType
from pitch_ledger.schema.history import MatchRecord

from .conftest import Built


def _record(built: Built) -> MatchRecord:
    sim, tracking = built
    return build_record(sim.match, sim.events, tracking)


def test_record_matches_the_recap_and_the_stats_tools(m0002: Built, tmp_path: Path) -> None:
    sim, tracking = m0002
    record = _record(m0002)
    assert (record.match_id, record.season, record.score_home, record.score_away) == (
        "M0002", "2026-27", 4, 1)  # fmt: skip
    assert str(record.kickoff_utc.date()) == "2026-08-29"  # matchday 2 (scenarios/matches.yaml)
    end = next(e for e in sim.events if e.event_type is EventType.PERIOD_END and e.period == 2)
    recap, _ = rm.recap(MatchEngine(sim.match, sim.events), end, "T01", "T02")
    home = record.team("T01")
    assert home is not None and home.result == "win" and home.venue == "home"
    assert (home.shots, home.xg, home.possession_pct) == (
        recap["shots_home"], recap["xg_home"], recap["possession_home"])  # fmt: skip
    assert record.team("T02").result == "loss"  # type: ignore[union-attr]
    assert len(record.goals) == 5 and record.hat_tricks == ["T01-P09"]
    assert record.player_goals("T01-P09") == 3

    from pitch_ledger.matchlab.io import write_match

    write_match(tmp_path / "M0002", sim.match, sim.teams, sim.players, sim.events, tracking)
    tools = StatsTools(tmp_path / "M0002")
    for line in record.players[:6] + [p for p in record.players if p.player_id == "T01-P09"]:
        stats = tools.get_player_stats(line.player_id)
        assert (line.goals, line.shots, line.passes, line.pass_accuracy) == (
            stats["goals"], stats["shots"], stats["passes"], stats["pass_accuracy"])  # fmt: skip
        assert line.distance_km == pytest.approx(stats["distance_km"], abs=0.01)


def test_unfinished_match_has_no_record(m0002: Built) -> None:
    sim, tracking = m0002
    first_half = [e for e in sim.events if e.period == 1]
    with pytest.raises(MatchNotFinished):
        build_record(sim.match, first_half, tracking)


def test_stores_keep_one_record_per_match_in_kickoff_order(
    m0001: Built, m0002: Built, tmp_path: Path
) -> None:
    first, second = _record(m0001), _record(m0002)
    store = FileHistory(tmp_path / "history")
    store.put(second)
    store.put(first)
    store.put(second)  # deterministic records: a second put replaces the first
    again = FileHistory(tmp_path / "history")
    found = again.season("Harbour Coast League", "2026-27")
    assert [r.match_id for r in found] == ["M0001", "M0002"]
    assert found[1] == second
    assert again.season("Harbour Coast League", "2025-26") == []
    assert InMemoryHistory([second, first]).season("Harbour Coast League", "2026-27") == found


def test_season_view_sees_only_earlier_matches(m0001: Built, m0002: Built) -> None:
    store = InMemoryHistory([_record(m0001), _record(m0002)])
    assert season_for(m0002[0].match, store).match_ids == ["M0001"]  # type: ignore[union-attr]
    assert season_for(m0001[0].match, store).match_ids == []  # type: ignore[union-attr]
    assert season_for(m0001[0].match, None) is None


def test_league_table(m0001: Built, m0002: Built) -> None:
    rows = league_table([_record(m0002), _record(m0001)])
    top, bottom = rows
    assert (top.team_id, top.played, top.won, top.drawn, top.points, top.form) == (
        "T01", 2, 1, 1, 4, ["D", "W"])  # fmt: skip
    assert (bottom.team_id, bottom.lost, bottom.goal_difference) == ("T02", 1, -3)


class FakeContainer:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.queries: list[dict[str, Any]] = []

    def upsert_item(self, body: dict[str, Any]) -> None:
        self.docs[body["id"]] = {**body, "_ts": 1, "_etag": "x"}

    def query_items(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.queries.append({"query": query, **kwargs})
        competition = kwargs["parameters"][0]["value"]
        season = kwargs["partition_key"]
        return [d for d in self.docs.values()
                if d["season"] == season and d["competition"] == competition]  # fmt: skip

    def delete_item(self, item: str, partition_key: str) -> None:
        self.docs.pop(item, None)


def test_cosmos_history_one_document_per_match_read_by_season(m0001: Built, m0002: Built) -> None:
    container = FakeContainer()
    store = CosmosHistory(container)
    store.put(_record(m0002))
    store.put(_record(m0001))
    store.put(_record(m0002))
    assert sorted(container.docs) == ["M0001", "M0002"]  # id = match ID, upserted
    found = store.season("Harbour Coast League", "2026-27")
    assert [r.match_id for r in found] == ["M0001", "M0002"] and found[1] == _record(m0002)
    assert container.queries[-1]["partition_key"] == "2026-27"  # one partition per season
