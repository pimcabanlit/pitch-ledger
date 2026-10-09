"""Engine metrics, timeline, windows and physical output."""

import copy
from typing import Any

import pytest

from pitch_ledger.engine import (
    MatchEngine,
    control_score,
    match_minute,
    pass_ball_speed_kmh,
    pass_difficulty_of,
    pass_length_m,
    physical,
    play_spans,
    shot_xg,
    turnovers,
)
from pitch_ledger.matchlab.generator import MatchSpec, SimulatedMatch, simulate_match
from pitch_ledger.schema import Event, TrackingSample
from tests.schema.test_models import WORKED_EXAMPLE, worked_event
from tests.validation.factories import baseline_rows, find, make_match

M = 60_000


def events_of(rows: list[dict[str, Any]]) -> list[Event]:
    return [Event.model_validate(r) for r in rows]


@pytest.fixture(scope="module")
def sim() -> SimulatedMatch:
    return simulate_match(MatchSpec(seed=4211))


# Derived metrics -----------------------------------------------------------
def test_worked_example_numbers() -> None:
    through = Event.model_validate(worked_event(WORKED_EXAMPLE[5]))
    shot = Event.model_validate(worked_event(WORKED_EXAMPLE[6]))
    assert pass_length_m(through) == pytest.approx(15.3, abs=0.05)
    assert pass_difficulty_of(through) == pytest.approx(31, abs=1)
    assert pass_ball_speed_kmh(through) == pytest.approx(68.8, abs=0.1)
    assert shot_xg(shot) == pytest.approx(0.29, abs=0.01)


def test_metrics_reject_wrong_event_types() -> None:
    receipt = Event.model_validate(worked_event(WORKED_EXAMPLE[1]))
    with pytest.raises(ValueError):
        pass_difficulty_of(receipt)
    with pytest.raises(ValueError):
        shot_xg(receipt)


# Timeline -----------------------------------------------------------------
def test_play_stops_at_out_of_play_and_restarts_at_throw_in() -> None:
    rows = baseline_rows()
    events = events_of(rows)
    spans = play_spans(events)
    out = find(rows, "out_of_play")
    throw = next(r for r in rows if r.get("details", {}).get("set_piece") == "throw_in")
    assert not any(s.start_ms <= out["clock_ms"] < s.end_ms and s.period == 1 for s in spans)
    assert any(s.start_ms == throw["clock_ms"] and s.team_id == "T02" for s in spans)


def test_no_live_time_after_goal_until_kick_off() -> None:
    rows = baseline_rows()
    goal = find(rows, "shot")
    kickoff = rows[rows.index(goal) + 1]
    spans = play_spans(events_of(rows))
    assert not any(
        s.period == 1 and goal["clock_ms"] <= s.start_ms < kickoff["clock_ms"] for s in spans
    )


def test_turnovers_attributed_to_cause() -> None:
    rows = baseline_rows()
    found = {t.event_id: t for t in turnovers(events_of(rows))}
    free_kick = next(r for r in rows if r.get("details", {}).get("set_piece") == "free_kick")
    out_pass = next(r for r in rows if r.get("outcome") == "out_of_play")
    tackle = find(rows, "tackle")
    assert found[free_kick["event_id"]].cause == "pass_incomplete"
    assert found[free_kick["event_id"]].team_id == "T02"
    assert found[out_pass["event_id"]].cause == "pass_out_of_play"
    assert found[tackle["event_id"]].cause == "lost_tackle"
    assert found[tackle["event_id"]].team_id == "T01"
    shot = find(rows, "shot")
    assert shot["event_id"] not in found  # a shot ends a possession without a turnover


def test_turnover_location_is_in_losing_team_frame() -> None:
    rows = copy.deepcopy(baseline_rows())
    tackle = find(rows, "tackle")
    tackle.update(start_x=80.0, end_x=80.0, start_y=10.0, end_y=10.0)
    t = next(t for t in turnovers(events_of(rows)) if t.event_id == tackle["event_id"])
    assert (t.x, t.y) == (25.0, 58.0)
    assert t.own_half


# Windows ------------------------------------------------------------------
def test_possession_shares_sum_to_one(sim: SimulatedMatch) -> None:
    engine = MatchEngine(sim.match, sim.events)
    for start in range(0, 40 * M, 5 * M):
        home = engine.team_window("T01", 1, start, start + 5 * M)
        away = engine.team_window("T02", 1, start, start + 5 * M)
        assert home.possession_share + away.possession_share == pytest.approx(1.0)
        assert 0 <= home.control <= 100 and 0 <= away.control <= 100


def test_live_and_batch_windows_agree(sim: SimulatedMatch) -> None:
    """A window computed mid-match equals the same window computed after full time."""
    full = MatchEngine(sim.match, sim.events)
    for end in (12 * M + 30_000, 27 * M, 41 * M + 15_000):
        live = MatchEngine(
            sim.match, [e for e in sim.events if e.period == 1 and e.clock_ms <= end]
        )
        for team in ("T01", "T02"):
            assert live.team_window(team, 1, end - 5 * M, end) == full.team_window(
                team, 1, end - 5 * M, end
            )


def test_window_counts_match_events(sim: SimulatedMatch) -> None:
    engine = MatchEngine(sim.match, sim.events)
    w = engine.team_window("T01", 2, 10 * M, 20 * M)
    events = engine.events_in(2, 10 * M, 20 * M)
    passes = [e for e in events if e.event_type == "pass" and e.team_id == "T01"
              and e.details.model_dump().get("set_piece") != "throw_in"]  # fmt: skip
    assert w.passes == len(passes)
    assert w.passes_completed == sum(e.outcome == "complete" for e in passes)
    assert w.turnovers == len(w.turnover_event_ids)
    if w.ppda is not None:
        assert w.ppda == pytest.approx(w.ppda_opponent_passes / w.ppda_defensive_actions)
    ids = {e.event_id for e in events}
    assert set(w.turnover_event_ids) <= ids and set(w.defensive_action_ids) <= ids


def test_control_score_components() -> None:
    assert control_score(0.8, 8.0, 0.95, 0) == pytest.approx(100.0)
    assert control_score(0.2, 0.0, 0.55, 6) == pytest.approx(0.0)
    assert control_score(0.5, 4.0, 0.75, 3) == pytest.approx(50.0)
    assert control_score(0.6, 4.0, 0.75, 3) > control_score(0.5, 4.0, 0.75, 3)
    assert control_score(0.5, 4.0, 0.75, 5) < control_score(0.5, 4.0, 0.75, 3)


def test_chaos_bounded(sim: SimulatedMatch) -> None:
    engine = MatchEngine(sim.match, sim.events)
    c = engine.chaos_window(1, 10 * M, 15 * M)
    assert 0 <= c.chaos <= 100
    assert 0 <= c.pressured_share <= 1


def test_match_minute() -> None:
    assert match_minute(2, 15 * M) == 60.0
    assert match_minute(1, 803_000) == pytest.approx(13.383, abs=0.001)
    assert make_match().home_team_id == "T01"


# Physical -----------------------------------------------------------------
def _sample(t: int, x: float, speed: float) -> TrackingSample:
    return TrackingSample(match_id="M0001", period=1, clock_ms=t * 1000, player_id="T01-P07",
                          x=x, y=10.0, speed_kmh=speed)  # fmt: skip


def test_physical_distance_bands_and_sprints() -> None:
    # 0 m, then 5 m (18 km/h), 6 m (21.6, HSR), 8 m (28.8, sprint), 8 m (sprint), 3 m, 8 m.
    xs = [0, 5, 11, 19, 27, 30, 38]
    speeds = [0, 18.0, 21.6, 28.8, 28.8, 10.8, 28.8]
    samples = [_sample(i, x, s) for i, (x, s) in enumerate(zip(xs, speeds, strict=True))]
    p = physical(samples)["T01-P07"]
    assert p.distance_m == pytest.approx(38.0)
    assert p.hsr_m == pytest.approx(6.0)
    assert p.sprint_m == pytest.approx(24.0)
    assert p.sprints == 2
    assert p.top_speed_kmh == 28.8
    window = physical(samples, period=1, start_ms=2000, end_ms=4000)["T01-P07"]
    assert window.distance_m == pytest.approx(16.0) and window.seconds == 2
