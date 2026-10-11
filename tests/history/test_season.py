"""Season facts: detected from earlier matches' records, recomputed by the Auditor's check."""

from datetime import timedelta
from pathlib import Path

import pytest

from pitch_ledger.agents.editorial import editorial_issues
from pitch_ledger.agents.phrases import choose
from pitch_ledger.engine.detector import detect
from pitch_ledger.engine.recompute import NotRecomputable, recompute_trigger
from pitch_ledger.engine.season import SeasonView, goal_metrics, has_season_facts, recap_metrics
from pitch_ledger.history import InMemoryHistory, build_record, season_for
from pitch_ledger.schema.enums import EventType, Outcome

from .conftest import Built


def _view(earlier: list[Built], match_built: Built) -> SeasonView:
    records = [build_record(s.match, s.events, t) for s, t in earlier]
    view = season_for(match_built[0].match, InMemoryHistory(records))
    assert view is not None
    return view


def test_goals_and_full_time_carry_season_facts_that_recompute(m0001: Built, m0002: Built) -> None:
    sim, tracking = m0002
    view = _view([m0001], m0002)
    triggers = detect(sim.match, sim.events, tracking, season=view)
    goals = [t for t in triggers if t.rule_id == "goal"]
    assert all(t.context["season"]["matches"] == ["M0001"] for t in goals)
    assert all(t.metrics["team_matches_before"] == 1 for t in goals)
    trick = next(t for t in goals if t.metrics["player_goals"] == 3)
    assert (trick.metrics["season_goals"], trick.metrics["season_hat_tricks_before"]) == (3, 0)
    full_time = next(t for t in triggers if t.rule_id == "full_time_recap")
    ft = full_time.metrics
    assert (ft["h2h_home_goals"], ft["h2h_away_goals"]) == (0, 0)
    # M0001 drawn, today won:
    assert (ft["home_form_played"], ft["home_form_wins"], ft["home_form_draws"]) == (2, 1, 1)
    half_time = next(t for t in triggers if t.rule_id == "half_time_recap")
    assert not has_season_facts(half_time.metrics) and "season" not in half_time.context
    for t in [*goals, full_time]:
        assert recompute_trigger(t, sim.match, sim.events, tracking, season=view) == t.metrics
    # Without the season there are no season facts, and an opening-day match has none either.
    plain = detect(sim.match, sim.events, tracking)
    assert not any(has_season_facts(t.metrics) for t in plain)
    opening = detect(m0001[0].match, m0001[0].events, m0001[1], season=_view([m0002], m0001))
    assert not any(has_season_facts(t.metrics) for t in opening)


def test_recompute_refuses_other_season_records(m0001: Built, m0002: Built) -> None:
    sim, tracking = m0002
    view = _view([m0001], m0002)
    trick = next(t for t in detect(sim.match, sim.events, tracking, season=view)
                 if t.metrics.get("player_goals") == 3)  # fmt: skip
    with pytest.raises(NotRecomputable, match="season records differ"):
        recompute_trigger(trick, sim.match, sim.events, tracking)  # no season given
    empty = SeasonView(view.competition, view.season, ())
    with pytest.raises(NotRecomputable, match="season records differ"):
        recompute_trigger(trick, sim.match, sim.events, tracking, season=empty)
    tampered = trick.model_copy(update={"metrics": {**trick.metrics, "season_goals": 9}})
    fresh = recompute_trigger(tampered, sim.match, sim.events, tracking, season=view)
    assert fresh["season_goals"] == 3  # the Auditor's check sees the difference


def test_form_and_last_meeting_from_the_other_clubs_side(m0001: Built, m0002: Built) -> None:
    """A later match with the clubs swapped: Corrowmere at home after losing 1-4 away."""
    later = m0002[0].match.model_copy(update={
        "match_id": "M0006", "home_team_id": "T02", "away_team_id": "T01",
        "kickoff_utc": m0002[0].match.kickoff_utc + timedelta(days=28),
    })  # fmt: skip
    records = [build_record(s.match, s.events, t) for s, t in (m0001, m0002)]
    view = SeasonView.before(later, records)
    metrics = recap_metrics(view, later, 0, 2)
    assert (metrics["h2h_home_goals"], metrics["h2h_away_goals"]) == (1, 4)
    assert (metrics["home_form_played"], metrics["home_form_draws"],
            metrics["home_form_losses"]) == (3, 1, 2)  # D, L, then today's L  # fmt: skip
    assert (metrics["away_form_played"], metrics["away_form_wins"]) == (3, 2)
    goal = next(e for e in m0002[0].events
                if e.event_type is EventType.SHOT and e.outcome is Outcome.GOAL
                and e.player_id == "T01-P09")  # fmt: skip
    assert goal_metrics(view, goal, player_goals=1)["season_goals"] == 4  # 3 earlier + 1


def test_season_copy_and_the_editorial_gate() -> None:
    values = {"player_goals": 1, "season_goals": 4, "team_matches_before": 2}
    assert choose("goal_context", values, "en").startswith(
        "It takes the scorer to {season_goals} league goals this season"
    )
    first = {"player_goals": 3, "season_goals": 3, "season_hat_tricks_before": 0}
    assert "first hat-trick of the league season" in choose("goal_context", first, "en")
    later = {**first, "season_hat_tricks_before": 1}
    assert "first" not in choose("goal_context", later, "en")
    text = "It is the first hat-trick of the league season."
    assert any("'season'" in i for i in editorial_issues(text, "overlay"))
    assert editorial_issues(text, "overlay", season=True) == []
    assert editorial_issues("Una marca histórica.", "overlay", "es", season=True) != []


def test_mcp_server_recomputes_season_facts_from_the_handed_over_records(
    m0001: Built, m0002: Built, tmp_path: Path
) -> None:
    import asyncio

    from pitch_ledger.matchlab.io import write_match, write_triggers
    from pitch_ledger.mcp_server.client import McpStats

    sim, tracking = m0002
    view = _view([m0001], m0002)
    match_dir = tmp_path / "M0002"
    write_match(match_dir, sim.match, sim.teams, sim.players, sim.events, tracking)
    triggers = detect(sim.match, sim.events, tracking, season=view)
    write_triggers(match_dir, triggers)
    trick = next(t for t in triggers if t.metrics.get("player_goals") == 3)

    async def run() -> dict[str, int | float]:
        async with McpStats(match_dir, view) as stats:
            return await stats.recompute_trigger(trick.trigger_id)

    assert asyncio.run(run()) == trick.metrics
