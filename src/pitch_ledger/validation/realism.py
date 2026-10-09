"""Realism bands (docs/event-schema.md, "Realism targets") and their measurement (MATCH-02).

Counting conventions: passes and pass accuracy exclude throw-ins; tackles count won and
lost; distance and top-speed bands apply to outfield players who played every second;
the shot-speed band applies to each shot.
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from pitch_ledger.schema import Event, Match, PassDetails, Player, ShotDetails, TrackingSample
from pitch_ledger.schema.enums import EventType, Outcome, Position, SetPiece
from pitch_ledger.validation.results import ValidationResult, violation


@dataclass(frozen=True)
class Band:
    metric: str
    target: str
    low: float
    high: float
    unit: str = ""


BANDS: dict[str, Band] = {
    b.metric: b
    for b in (
        Band("passes_both_teams", "~880", 750, 1050),
        Band("passes_home", "style-dependent", 350, 750),
        Band("passes_away", "style-dependent", 350, 750),
        Band("pass_accuracy_pct", "~82%", 75, 88, "%"),
        Band("shots_both_teams", "~26", 18, 36),
        Band("goals", "~3", 0, 7),
        Band("tackles_both_teams", "~31", 22, 42),
        Band("distance_outfield_km", "10-11.5 km", 9, 13, "km"),
        Band("top_speed_kmh", "~31 km/h", 28, 36, "km/h"),
        Band("shot_speed_kmh", "> 100 notable", 50, 130, "km/h"),
    )
}


@dataclass(frozen=True)
class RealismCheck:
    """One measured metric against its band. ``values`` holds one entry per player or shot
    for per-item metrics; the check passes when every value lies in the band."""

    band: Band
    values: tuple[float, ...]

    @property
    def ok(self) -> bool:
        return all(self.band.low <= v <= self.band.high for v in self.values)

    def summary(self) -> str:
        if not self.values:
            return "n/a"
        if len(self.values) == 1:
            return _fmt(self.values[0])
        mean = sum(self.values) / len(self.values)
        return (
            f"mean {_fmt(mean)} (min {_fmt(min(self.values))}, max {_fmt(max(self.values))},"
            f" n={len(self.values)})"
        )


def _fmt(value: float) -> str:
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def _is_counted_pass(event: Event) -> bool:
    return (
        event.event_type is EventType.PASS
        and isinstance(event.details, PassDetails)
        and event.details.set_piece is not SetPiece.THROW_IN
    )


def player_distances_m(tracking: Sequence[TrackingSample]) -> dict[str, float]:
    """Distance covered per player (m): sum of straight lines between consecutive seconds."""
    total: dict[str, float] = defaultdict(float)
    last: dict[tuple[str, int], TrackingSample] = {}
    for s in tracking:
        key = (s.player_id, s.period)
        prev = last.get(key)
        if prev is not None and s.clock_ms - prev.clock_ms == 1000:
            total[s.player_id] += math.hypot(s.x - prev.x, s.y - prev.y)
        last[key] = s
    return dict(total)


def measure(
    match: Match,
    players: Sequence[Player],
    events: Sequence[Event],
    tracking: Sequence[TrackingSample],
) -> list[RealismCheck]:
    """Measure every realism metric for one match."""
    passes = [e for e in events if _is_counted_pass(e)]
    complete = sum(1 for e in passes if e.outcome is Outcome.COMPLETE)
    shots = [e for e in events if e.event_type is EventType.SHOT]
    goals = sum(1 for e in shots if e.outcome is Outcome.GOAL)
    tackles = sum(1 for e in events if e.event_type is EventType.TACKLE)
    accuracy = 100.0 * complete / len(passes) if passes else 0.0

    samples_per_player: dict[str, int] = defaultdict(int)
    top_speed: dict[str, float] = defaultdict(float)
    for s in tracking:
        samples_per_player[s.player_id] += 1
        top_speed[s.player_id] = max(top_speed[s.player_id], s.speed_kmh)
    full = max(samples_per_player.values(), default=0)
    outfield = {p.player_id for p in players if p.position is not Position.GK}
    full_match = sorted(p for p, n in samples_per_player.items() if n == full and p in outfield)
    dist = player_distances_m(tracking)

    def m(name: str, *values: float) -> RealismCheck:
        return RealismCheck(BANDS[name], tuple(values))

    shot_speeds = [e.details.shot_speed_kmh for e in shots if isinstance(e.details, ShotDetails)]
    return [
        m("passes_both_teams", len(passes)),
        m("passes_home", sum(1 for e in passes if e.team_id == match.home_team_id)),
        m("passes_away", sum(1 for e in passes if e.team_id == match.away_team_id)),
        m("pass_accuracy_pct", round(accuracy, 1)),
        m("shots_both_teams", len(shots)),
        m("goals", goals),
        m("tackles_both_teams", tackles),
        m("distance_outfield_km", *(round(dist.get(p, 0.0) / 1000, 2) for p in full_match)),
        m("top_speed_kmh", *(top_speed[p] for p in full_match)),
        m("shot_speed_kmh", *shot_speeds),
    ]


def check_realism(checks: Sequence[RealismCheck]) -> list[ValidationResult]:
    """MATCH-02: flag each metric outside its band."""
    return [
        violation(
            "MATCH-02",
            None,
            f"{c.band.metric} {c.summary()} outside {_fmt(c.band.low)}-{_fmt(c.band.high)}",
        )
        for c in checks
        if not c.ok
    ]
