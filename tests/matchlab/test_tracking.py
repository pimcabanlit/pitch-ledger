"""1 Hz tracking is complete, physically plausible and consistent with events."""

import itertools
import math
import statistics
from collections import Counter, defaultdict

import pytest

from pitch_ledger.matchlab.generator import MatchSpec, SimulatedMatch, simulate_match
from pitch_ledger.matchlab.tracking import generate_tracking
from pitch_ledger.schema import PassDetails, SubstitutionDetails, TrackingSample
from pitch_ledger.schema.enums import EventType, Position, SetPiece
from pitch_ledger.validation.realism import player_distances_m


@pytest.fixture(scope="module")
def sim() -> SimulatedMatch:
    return simulate_match(MatchSpec(seed=4211))


@pytest.fixture(scope="module")
def tracking(sim: SimulatedMatch) -> list[TrackingSample]:
    return generate_tracking(sim)


def test_reproducible(sim: SimulatedMatch, tracking: list[TrackingSample]) -> None:
    again = generate_tracking(sim)
    assert again[:500] == tracking[:500] and again[-500:] == tracking[-500:]
    assert len(again) == len(tracking)


def test_one_sample_per_on_pitch_player_per_second(
    sim: SimulatedMatch, tracking: list[TrackingSample]
) -> None:
    per_tick = Counter((s.period, s.clock_ms) for s in tracking)
    ends = {e.period: e.clock_ms for e in sim.events if e.event_type is EventType.PERIOD_END}
    for period, end_ms in ends.items():
        ticks = [k for k in per_tick if k[0] == period]
        assert len(ticks) == end_ms // 1000 + 1
    assert set(per_tick.values()) <= {20, 21, 22}
    assert 110_000 < len(tracking) < 140_000


def test_speeds_and_bounds(tracking: list[TrackingSample]) -> None:
    assert max(s.speed_kmh for s in tracking) < 37
    assert all(0 <= s.x <= 105 and 0 <= s.y <= 68 for s in tracking)


def test_speed_equals_displacement(tracking: list[TrackingSample]) -> None:
    last: dict[tuple[str, int], TrackingSample] = {}
    for s in tracking[:20_000]:
        prev = last.get((s.player_id, s.period))
        if prev is not None:
            moved = math.hypot(s.x - prev.x, s.y - prev.y) * 3.6
            assert s.speed_kmh == pytest.approx(moved, abs=0.06)
        last[(s.player_id, s.period)] = s


def test_actors_are_at_their_events(sim: SimulatedMatch, tracking: list[TrackingSample]) -> None:
    """Runs are timed: at the sample nearest an event the actor is on the ball."""
    at = {(s.player_id, s.period, s.clock_ms): s for s in tracking}
    gaps = []
    for e in sim.events:
        if e.player_id and e.start_x is not None and e.start_y is not None:
            s = at.get((e.player_id, e.period, round(e.clock_ms / 1000) * 1000))
            if s is not None:
                gaps.append(math.hypot(s.x - e.start_x, s.y - e.start_y))
    assert statistics.median(gaps) < 0.5
    assert statistics.quantiles(gaps, n=10)[8] < 1.5
    assert sum(g > 3 for g in gaps) / len(gaps) < 0.03


def test_dead_balls_follow_the_laws(sim: SimulatedMatch, tracking: list[TrackingSample]) -> None:
    """At each restart, opponents keep their distance (free kicks, corners, goal kicks)."""
    at: dict[tuple[int, int], dict[str, TrackingSample]] = defaultdict(dict)
    for s in tracking:
        at[(s.period, s.clock_ms)][s.player_id] = s
    seen: Counter[str] = Counter()
    for prev, e in itertools.pairwise(sim.events):
        if not isinstance(e.details, PassDetails) or e.details.set_piece is SetPiece.NONE:
            continue
        if e.clock_ms == prev.clock_ms or e.start_x is None or e.start_y is None:
            continue  # the kick-off of a half: players are placed, not walking in
        kind = e.details.set_piece
        seen[kind] += 1
        for pid, s in at[(e.period, round(e.clock_ms / 1000) * 1000)].items():
            if pid.startswith(str(e.team_id)):
                continue
            x, y = 105 - s.x, 68 - s.y  # the restarting team's frame
            if kind in (SetPiece.FREE_KICK, SetPiece.CORNER):
                assert math.hypot(x - e.start_x, y - e.start_y) > 8.5, (pid, e.event_id)
            if kind is SetPiece.GOAL_KICK:
                assert not (x < 16.5 and 13.84 < y < 54.16), (pid, e.event_id)
    assert seen[SetPiece.FREE_KICK] and seen[SetPiece.GOAL_KICK] and seen[SetPiece.CORNER]


def test_each_half_kicks_off_from_own_halves(
    sim: SimulatedMatch, tracking: list[TrackingSample]
) -> None:
    for period in (1, 2):
        first = [s for s in tracking if s.period == period and s.clock_ms == 0]
        assert len(first) >= 20
        assert all(s.x <= 52.5 for s in first)  # own frame: every team's own half is x < 52.5


def test_substitutes_appear_only_after_coming_on(
    sim: SimulatedMatch, tracking: list[TrackingSample]
) -> None:
    first_seen: dict[str, tuple[int, int]] = {}
    last_seen: dict[str, tuple[int, int]] = {}
    for s in tracking:
        first_seen.setdefault(s.player_id, (s.period, s.clock_ms))
        last_seen[s.player_id] = (s.period, s.clock_ms)
    subs = [e for e in sim.events if e.event_type is EventType.SUBSTITUTION]
    assert subs
    for e in subs:
        assert isinstance(e.details, SubstitutionDetails)
        assert first_seen[e.details.player_in_id] >= (e.period, e.clock_ms)
        assert last_seen[e.details.player_out_id] <= (e.period, e.clock_ms + 1000)


def test_physical_output_in_realistic_ranges(
    sim: SimulatedMatch, tracking: list[TrackingSample]
) -> None:
    samples = Counter(s.player_id for s in tracking)
    full = max(samples.values())
    keepers = {p.player_id for p in sim.players if p.position is Position.GK}
    dist = player_distances_m(tracking)
    top: dict[str, float] = defaultdict(float)
    for s in tracking:
        top[s.player_id] = max(top[s.player_id], s.speed_kmh)
    outfield_full = [p for p, n in samples.items() if n == full and p not in keepers]
    assert len(outfield_full) >= 10
    for p in outfield_full:
        assert 9_000 <= dist[p] <= 13_000
        assert 28 <= top[p] <= 36
