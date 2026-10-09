"""Team and match state over time windows (docs/event-schema.md, "Team and match state").

Windows are ``(start_ms, end_ms]`` within one period. Queries only read events up to
``end_ms``, so the same call gives the same answer live (mid-match) and in batch.

Control score (0-100) is a weighted average of four components, each mapped linearly
onto 0-100 and clipped:

| Component | 0 at | 100 at | Weight |
| --- | --- | --- | --- |
| Possession share | 20% | 80% | 0.40 |
| Completed passes per possession | 0 | 8 | 0.20 |
| Pass accuracy | 55% | 95% | 0.20 |
| Own-half turnovers | 6 | 0 | 0.20 |

Chaos level (0-100) is the mean of possession changes per minute (0 to 3), share of
on-ball events under pressure (0 to 50%) and duels per minute (0 to 2).
"""

import bisect
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cached_property

from pitch_ledger.engine.metrics import is_counted_pass
from pitch_ledger.engine.timeline import ON_BALL_TYPES, PlaySpan, Turnover, play_spans, turnovers
from pitch_ledger.schema import Event, Match
from pitch_ledger.schema.enums import EventType, Outcome

WINDOW_MS = 5 * 60_000
PPDA_ZONE_X = 63.0
"""Opponent passes count for PPDA in their own 60% of the pitch (x < 63 in their frame)."""

CONTROL_COMPONENTS: dict[str, tuple[float, float, float]] = {
    # name: (value at 0, value at 100, weight)
    "possession_share": (0.20, 0.80, 0.40),
    "passes_per_possession": (0.0, 8.0, 0.20),
    "pass_accuracy": (0.55, 0.95, 0.20),
    "own_half_turnovers": (6.0, 0.0, 0.20),
}
CHAOS_COMPONENTS: dict[str, tuple[float, float]] = {
    "possession_changes_per_min": (0.0, 3.0),
    "pressured_share": (0.0, 0.5),
    "duels_per_min": (0.0, 2.0),
}
DUEL_TYPES = frozenset({EventType.TACKLE, EventType.DRIBBLE, EventType.FOUL})
DEFENSIVE_ACTIONS = frozenset({EventType.TACKLE, EventType.INTERCEPTION, EventType.FOUL})


def scale(value: float, at_zero: float, at_hundred: float) -> float:
    """Map ``value`` linearly so ``at_zero`` -> 0 and ``at_hundred`` -> 100, clipped."""
    pct = (value - at_zero) / (at_hundred - at_zero) * 100.0
    return max(0.0, min(100.0, pct))


@dataclass(frozen=True)
class TeamWindow:
    """One team's numbers over one window. Ratios are None when undefined."""

    team_id: str
    period: int
    start_ms: int
    end_ms: int
    possession_ms: int
    possession_share: float
    possessions: int
    passes: int
    passes_completed: int
    pass_accuracy: float | None
    passes_per_possession: float
    turnovers: int
    own_half_turnovers: int
    ppda: float | None
    ppda_opponent_passes: int
    ppda_defensive_actions: int
    tempo: float | None
    control: float
    turnover_event_ids: tuple[str, ...] = field(default=())
    defensive_action_ids: tuple[str, ...] = field(default=())

    @property
    def start_minute(self) -> float:
        return match_minute(self.period, self.start_ms)

    @property
    def end_minute(self) -> float:
        return match_minute(self.period, self.end_ms)


@dataclass(frozen=True)
class ChaosWindow:
    period: int
    start_ms: int
    end_ms: int
    possession_changes_per_min: float
    pressured_share: float
    duels_per_min: float
    chaos: float


def match_minute(period: int, clock_ms: int) -> float:
    """Match minute: period 2 clock 15:00 is minute 60."""
    return (45.0 if period == 2 else 0.0) + clock_ms / 60_000


def control_score(
    possession_share: float,
    passes_per_possession: float,
    pass_accuracy: float | None,
    own_half_turnovers: int,
) -> float:
    """Weighted control score (0-100) from the four components."""
    values = {
        "possession_share": possession_share,
        "passes_per_possession": passes_per_possession,
        "pass_accuracy": pass_accuracy if pass_accuracy is not None else 0.55,
        "own_half_turnovers": float(own_half_turnovers),
    }
    return sum(
        weight * scale(values[name], zero, hundred)
        for name, (zero, hundred, weight) in CONTROL_COMPONENTS.items()
    )


class MatchEngine:
    """Deterministic stats over a match's event stream (complete or still arriving)."""

    def __init__(self, match: Match, events: Sequence[Event] = ()) -> None:
        self.match = match
        self.events: list[Event] = []
        self.extend(events)

    @property
    def teams(self) -> tuple[str, str]:
        return self.match.home_team_id, self.match.away_team_id

    def other(self, team_id: str) -> str:
        home, away = self.teams
        return away if team_id == home else home

    def extend(self, events: Sequence[Event]) -> None:
        """Append newly arrived events (in order) and reset caches."""
        self.events.extend(events)
        for name in ("_by_period", "_spans", "_turnovers"):
            self.__dict__.pop(name, None)

    @cached_property
    def _by_period(self) -> dict[int, tuple[list[int], list[Event]]]:
        out: dict[int, tuple[list[int], list[Event]]] = {}
        for e in self.events:
            clocks, rows = out.setdefault(e.period, ([], []))
            clocks.append(e.clock_ms)
            rows.append(e)
        return out

    @cached_property
    def _spans(self) -> list[PlaySpan]:
        return play_spans(self.events)

    @cached_property
    def _turnovers(self) -> list[Turnover]:
        return turnovers(self.events)

    def events_in(self, period: int, start_ms: int, end_ms: int) -> list[Event]:
        """Events with ``start_ms < clock_ms <= end_ms`` in ``period``."""
        clocks, rows = self._by_period.get(period, ([], []))
        lo = bisect.bisect_right(clocks, start_ms)
        hi = bisect.bisect_right(clocks, end_ms)
        return rows[lo:hi]

    def possession_ms(self, period: int, start_ms: int, end_ms: int) -> dict[str, int]:
        """Live-ball milliseconds per team inside the window."""
        out = dict.fromkeys(self.teams, 0)
        spans = self._spans_until(period, end_ms)
        for span in spans:
            lo, hi = max(span.start_ms, start_ms), min(span.end_ms, end_ms)
            if hi > lo:
                out[span.team_id] = out.get(span.team_id, 0) + hi - lo
        return out

    def _spans_until(self, period: int, end_ms: int) -> list[PlaySpan]:
        """Spans as known at ``end_ms``. A span is fixed once the next event arrives, so
        the full-stream spans apply; only a still-open last span is cut at ``end_ms``."""
        clocks, rows = self._by_period.get(period, ([], []))
        n = bisect.bisect_right(clocks, end_ms)
        if rows and n == len(rows) and rows[-1].event_type is not EventType.PERIOD_END:
            return play_spans(rows, until_ms={period: end_ms})
        return [s for s in self._spans if s.period == period and s.start_ms < end_ms]

    def turnovers_in(self, period: int, start_ms: int, end_ms: int) -> list[Turnover]:
        """Turnovers caused in the window and already confirmed by ``end_ms``."""
        return [
            t
            for t in self._turnovers
            if t.period == period and start_ms < t.clock_ms <= end_ms and t.detected_ms <= end_ms
        ]

    def team_window(self, team_id: str, period: int, start_ms: int, end_ms: int) -> TeamWindow:
        """Every team-state metric for ``team_id`` over ``(start_ms, end_ms]``."""
        opp = self.other(team_id)
        events = self.events_in(period, start_ms, end_ms)
        poss_ms = self.possession_ms(period, start_ms, end_ms)
        total_ms = sum(poss_ms.values())
        share = poss_ms[team_id] / total_ms if total_ms else 0.5
        possessions = len({e.possession_id for e in events if e.possession_team_id == team_id})
        passes = [e for e in events if is_counted_pass(e) and e.team_id == team_id]
        completed = sum(e.outcome is Outcome.COMPLETE for e in passes)
        accuracy = completed / len(passes) if passes else None
        ppp = completed / possessions if possessions else 0.0
        lost = [t for t in self.turnovers_in(period, start_ms, end_ms) if t.team_id == team_id]
        own_half = sum(t.own_half for t in lost)
        opp_passes = [
            e
            for e in events
            if e.event_type is EventType.PASS
            and e.team_id == opp
            and e.start_x is not None
            and e.start_x < PPDA_ZONE_X
        ]
        actions = [
            e
            for e in events
            if e.event_type in DEFENSIVE_ACTIONS
            and e.team_id == team_id
            and e.start_x is not None
            and e.start_x > 105.0 - PPDA_ZONE_X
        ]
        ppda = len(opp_passes) / len(actions) if actions else None
        minutes_on_ball = poss_ms[team_id] / 60_000
        tempo = len(passes) / minutes_on_ball if minutes_on_ball > 0 else None
        return TeamWindow(
            team_id=team_id,
            period=period,
            start_ms=start_ms,
            end_ms=end_ms,
            possession_ms=poss_ms[team_id],
            possession_share=share,
            possessions=possessions,
            passes=len(passes),
            passes_completed=completed,
            pass_accuracy=accuracy,
            passes_per_possession=ppp,
            turnovers=len(lost),
            own_half_turnovers=own_half,
            ppda=ppda,
            ppda_opponent_passes=len(opp_passes),
            ppda_defensive_actions=len(actions),
            tempo=tempo,
            control=control_score(share, ppp, accuracy, own_half),
            turnover_event_ids=tuple(t.event_id for t in lost),
            defensive_action_ids=tuple(e.event_id for e in actions),
        )

    def chaos_window(self, period: int, start_ms: int, end_ms: int) -> ChaosWindow:
        """Match chaos level over ``(start_ms, end_ms]``."""
        events = self.events_in(period, start_ms, end_ms)
        minutes = max((end_ms - start_ms) / 60_000, 1e-9)
        prev_team: str | None = None
        changes = 0
        for e in self.events_in(period, 0, end_ms):
            changed = prev_team is not None and e.possession_team_id != prev_team
            if changed and e.clock_ms > start_ms:
                changes += 1
            prev_team = e.possession_team_id
        on_ball = [e for e in events if e.event_type in ON_BALL_TYPES and e.player_id]
        pressured = sum(e.under_pressure for e in on_ball) / len(on_ball) if on_ball else 0.0
        duels = sum(e.event_type in DUEL_TYPES for e in events)
        values = {
            "possession_changes_per_min": changes / minutes,
            "pressured_share": pressured,
            "duels_per_min": duels / minutes,
        }
        chaos = sum(scale(values[k], lo, hi) for k, (lo, hi) in CHAOS_COMPONENTS.items()) / 3
        return ChaosWindow(
            period,
            start_ms,
            end_ms,
            values["possession_changes_per_min"],
            pressured,
            values["duels_per_min"],
            chaos,
        )

    def period_end_ms(self, period: int) -> int | None:
        for e in self.events_in(period, -1, 10**9):
            if e.event_type is EventType.PERIOD_END:
                return e.clock_ms
        return None
