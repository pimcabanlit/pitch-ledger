"""The simulator is reproducible, valid, and follows the doc's models and conventions."""

import math
from itertools import pairwise

import pytest

from pitch_ledger.core.formulas import (
    distance,
    expected_goals,
    forward_progress,
    pass_completion_probability,
)
from pitch_ledger.core.pitch import flip
from pitch_ledger.matchlab.generator import MatchSpec, SimulatedMatch, simulate_match
from pitch_ledger.matchlab.roster import load_league
from pitch_ledger.matchlab.styles import Modifiers
from pitch_ledger.schema import Event, PassDetails, ShotDetails
from pitch_ledger.schema.enums import EventType, Outcome, PassHeight, PassType
from pitch_ledger.validation import validate_match


@pytest.fixture(scope="module")
def baseline() -> SimulatedMatch:
    return simulate_match(MatchSpec(seed=4211))


@pytest.fixture(scope="module")
def many() -> list[SimulatedMatch]:
    return [simulate_match(MatchSpec(seed=s)) for s in range(100, 112)]


def test_same_seed_same_match(baseline: SimulatedMatch) -> None:
    again = simulate_match(MatchSpec(seed=4211))
    assert again.match == baseline.match
    assert [e.model_dump_json() for e in again.events] == [
        e.model_dump_json() for e in baseline.events
    ]


def test_different_seed_different_match(baseline: SimulatedMatch) -> None:
    other = simulate_match(MatchSpec(seed=4212))
    assert [e.event_type for e in other.events] != [e.event_type for e in baseline.events]


def test_match_row_records_seed_and_version(baseline: SimulatedMatch) -> None:
    m = baseline.match
    assert (m.random_seed, m.scenario_id, m.generator_version) == (4211, "none", "0.1.0")
    assert m.venue == load_league().club("T01").venue


@pytest.mark.parametrize(
    ("home", "away", "seed"),
    [("T01", "T02", 1), ("T03", "T04", 2), ("T05", "T06", 3), ("T04", "T01", 4)],
)
def test_every_event_rule_passes_for_any_matchup(home: str, away: str, seed: int) -> None:
    sim = simulate_match(MatchSpec(home_team_id=home, away_team_id=away, seed=seed))
    report = validate_match(sim.match, sim.players, sim.events, realism=False)
    assert report.results == [], report.counts()


def test_many_seeds_produce_no_rejects(many: list[SimulatedMatch]) -> None:
    for sim in many:
        report = validate_match(sim.match, sim.players, sim.events, realism=False)
        assert report.ok and report.results == [], (sim.match.random_seed, report.counts())


def _pass_p(e: Event) -> float:
    assert isinstance(e.details, PassDetails)
    assert e.start_x is not None and e.start_y is not None
    assert e.end_x is not None and e.end_y is not None
    return pass_completion_probability(
        distance(e.start_x, e.start_y, e.end_x, e.end_y),
        e.under_pressure,
        forward_progress(e.start_x, e.end_x),
        e.details.height is PassHeight.HIGH,
        e.details.pass_type in (PassType.THROUGH_BALL, PassType.CROSS),
    )


def test_pass_outcomes_agree_with_difficulty_model(many: list[SimulatedMatch]) -> None:
    """Recomputed from the stored rows, the model's expected completions match the
    observed ones (pass rows store the intended target as their end)."""
    expected, completed, var = 0.0, 0, 0.0
    for sim in many:
        for e in sim.events:
            if e.event_type is EventType.PASS:
                p = _pass_p(e)
                expected += p
                var += p * (1 - p)
                completed += e.outcome is Outcome.COMPLETE
    assert abs(completed - expected) < 3.5 * math.sqrt(var)


def test_goals_track_expected_goals(many: list[SimulatedMatch]) -> None:
    goals, xg_sum, var = 0, 0.0, 0.0
    for sim in many:
        for e in sim.events:
            if e.event_type is not EventType.SHOT:
                continue
            assert isinstance(e.details, ShotDetails)
            assert e.start_x is not None and e.start_y is not None
            xg = expected_goals(
                e.start_x, e.start_y,
                header=e.details.shot_type == "header",
                through_ball_assist=e.details.assist_type is PassType.THROUGH_BALL,
                penalty=e.details.shot_type == "penalty",
            )  # fmt: skip
            goals += e.outcome is Outcome.GOAL
            xg_sum += xg
            var += xg * (1 - xg)
    assert abs(goals - xg_sum) < 3.5 * math.sqrt(var)


def test_defender_events_are_stored_flipped(baseline: SimulatedMatch) -> None:
    by_id = {e.event_id: e for e in baseline.events}
    checked = 0
    for e in baseline.events:
        if e.event_type is EventType.PRESSURE:
            target = by_id[e.related_event_ids[0]]
            if target.event_type is EventType.BALL_RECEIPT:
                assert target.team_id != e.team_id
                assert target.end_x is not None and target.end_y is not None
                fx, fy = flip(target.end_x, target.end_y)
                assert (e.start_x, e.start_y) == pytest.approx((fx, fy), abs=0.11)
                checked += 1
    assert checked > 20


def test_shots_attack_toward_x_105(baseline: SimulatedMatch) -> None:
    shots = [e for e in baseline.events if e.event_type is EventType.SHOT]
    assert shots and all(e.start_x is not None and e.start_x > 60 for e in shots)


def test_goal_kick_off_and_score_bookkeeping(many: list[SimulatedMatch]) -> None:
    for sim in many:
        for a, b in pairwise(sim.events):
            if a.event_type is EventType.SHOT and a.outcome is Outcome.GOAL:
                assert b.score_home + b.score_away == a.score_home + a.score_away + 1


def test_players_on_pitch_counts_follow_sendings_off(many: list[SimulatedMatch]) -> None:
    for sim in many:
        reds = 0
        for e in sim.events:
            assert e.players_on_pitch_home + e.players_on_pitch_away == 22 - reds
            if e.event_type is EventType.CARD and e.outcome is Outcome.SECOND_YELLOW:
                reds += 1


def test_substitutions_within_limits(many: list[SimulatedMatch]) -> None:
    for sim in many:
        for team in (sim.match.home_team_id, sim.match.away_team_id):
            subs = [e for e in sim.events
                    if e.event_type is EventType.SUBSTITUTION and e.team_id == team]  # fmt: skip
            assert 0 < len(subs) <= 5
            assert all(e.period == 2 for e in subs)


def test_modifier_hook_shifts_pass_success() -> None:
    def collapse(team_id: str, period: int, clock_ms: int) -> Modifiers:
        return Modifiers(pass_success_add=-0.25) if team_id == "T02" else Modifiers()

    def rate(sim: SimulatedMatch) -> float:
        passes = [e for e in sim.events if e.event_type is EventType.PASS and e.team_id == "T02"]
        return sum(e.outcome is Outcome.COMPLETE for e in passes) / len(passes)

    spec = MatchSpec(seed=7)
    assert rate(simulate_match(spec, collapse)) < rate(simulate_match(spec)) - 0.1


def test_same_team_twice_rejected() -> None:
    with pytest.raises(ValueError):
        simulate_match(MatchSpec(home_team_id="T01", away_team_id="T01"))
