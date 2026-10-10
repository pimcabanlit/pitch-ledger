"""One match as the live service sees it: rows so far, and triggers once they settle.

The detector is causal (a rule only reads data up to the moment it fires), so re-running
it over everything received so far finds the same triggers as a run over the finished
match. A trigger is emitted once the feed is ``settle_ms`` of match time past it, so rules
that look a few seconds ahead (a pass completes at its receipt) have their data, and at
full time everything left is emitted. Triggers get IDs in emission order, which is match
order, so a live run numbers its triggers like a batch run.

Once the match reference has arrived, every row goes through the ingest validation rules
(docs/event-schema.md): a rejected row is dropped before the engine, and ``add`` returns
the rule results so the service can raise ops alerts. A feed joined mid-match (first event
after ``seq_no`` 1) gets only the rules that need no earlier rows.
"""

from dataclasses import dataclass, field
from typing import Any

from pitch_ledger.engine.detector import detect
from pitch_ledger.engine.windows import MatchEngine
from pitch_ledger.eventstore.rows import STEP_MS, WINDOW_MS, team_state_row
from pitch_ledger.matchlab.io import MatchFiles
from pitch_ledger.schema import Event, Match, Player, Team, TrackingSample, Trigger
from pitch_ledger.validation import MatchValidator, RuleAction, ValidationResult
from pitch_ledger.validation.validator import check_tracking_rows

Position = tuple[int, int]  # (period, clock_ms): first-half stoppage comes before period 2


def trigger_key(t: Trigger) -> tuple[Any, ...]:
    return (t.rule_id, t.period, t.clock_ms, t.subject.team_id, t.subject.player_id)


@dataclass
class LiveMatch:
    match_id: str
    reference: dict[str, Any] | None = None
    events_raw: list[dict[str, Any]] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    tracking_raw: list[dict[str, Any]] = field(default_factory=list)
    tracking: list[TrackingSample] = field(default_factory=list)
    emitted: list[Trigger] = field(default_factory=list)
    full_time: bool = False
    in_play: bool = False  # play is on: the last event is not a period end
    validator: MatchValidator | None = None
    _players: list[Player] = field(default_factory=list)
    _keys: set[tuple[Any, ...]] = field(default_factory=set)
    _snapshot_ms: dict[int, int] = field(default_factory=dict)  # period -> last snapshot end

    @property
    def match(self) -> Match | None:
        return Match.model_validate(self.reference["match"]) if self.reference else None

    def files(self) -> MatchFiles:
        """The match so far, as the readers of a match directory expect it."""
        assert self.reference is not None
        return MatchFiles(
            match=Match.model_validate(self.reference["match"]),
            teams=[Team.model_validate(t) for t in self.reference["teams"]],
            players=[Player.model_validate(p) for p in self.reference["players"]],
            events=list(self.events_raw),
            tracking=list(self.tracking_raw),
        )

    def add(self, kind: str, row: dict[str, Any]) -> list[ValidationResult]:
        """Take one feed row; returns its validation results (a reject is not kept)."""
        if kind == "match":
            self.reference = row
            self._players = [Player.model_validate(p) for p in row["players"]]
            self.validator = MatchValidator(Match.model_validate(row["match"]), self._players)
            return []
        if kind == "event":
            results: list[ValidationResult] = []
            if self.validator is None:
                event: Event | None = Event.model_validate(row)
            else:
                if not self.events and row.get("seq_no") != 1:  # joined mid-match
                    self.validator = MatchValidator(self.validator.match, self._players, False)
                event, results = self.validator.check(row)
            if event is None or rejected(results):
                return results
            self.events_raw.append(row)
            self.events.append(event)
            self.in_play = event.event_type.value != "period_end"  # a stream may start mid-half
            if event.event_type.value == "period_end" and event.period == 2:
                self.full_time = True
            return results
        if kind == "tracking":
            if self.validator is None:
                samples, results = [TrackingSample.model_validate(row)], []
            else:
                samples, results = check_tracking_rows(self.match_id, [row])
            if samples and not rejected(results):
                self.tracking_raw.append(row)
                self.tracking.append(samples[0])
            return results
        return []

    def finish_validation(self) -> list[ValidationResult]:
        """End-of-match checks (pending look-ahead rules, one start and end per period);
        call once, at full time."""
        return self.validator.finish() if self.validator is not None else []

    @property
    def last_event_id(self) -> str | None:
        return self.events[-1].event_id if self.events else None

    def position(self) -> Position:
        """How far the feed has got: the latest (period, clock) received."""
        last = [(e.period, e.clock_ms) for e in self.events[-1:]]
        last += [(s.period, s.clock_ms) for s in self.tracking[-1:]]
        return max(last, default=(1, 0))

    def _settled(self, t: Trigger, settle_ms: int) -> bool:
        period, clock = self.position()
        return t.period < period or (t.period == period and t.clock_ms + settle_ms <= clock)

    def settled_triggers(self, settle_ms: int, final: bool = False) -> list[Trigger]:
        """New triggers whose moment the feed has passed by ``settle_ms`` (all of them at
        full time or when ``final``), numbered in order."""
        match = self.match
        if match is None or not self.events:
            return []
        everything = final or self.full_time
        fresh = [
            t
            for t in detect(match, self.events, self.tracking)
            if trigger_key(t) not in self._keys and (everything or self._settled(t, settle_ms))
        ]
        fresh.sort(key=lambda t: (t.period, t.clock_ms, t.trigger_id))
        numbered = []
        for t in fresh:
            n = len(self.emitted) + 1
            t = t.model_copy(update={"trigger_id": f"{self.match_id}-T{n:04d}"})
            self.emitted.append(t)
            self._keys.add(trigger_key(t))
            numbered.append(t)
        return numbered

    def team_state_rows(self) -> list[dict[str, Any]]:
        """Engine snapshots for whole minutes completed since the last call."""
        match = self.match
        if match is None or not self.events:
            return []
        engine = MatchEngine(match, self.events)
        rows = []
        for period in sorted({e.period for e in self.events}):
            reached = max(e.clock_ms for e in self.events if e.period == period)
            done = self._snapshot_ms.get(period, 0)
            for end_ms in range(done + STEP_MS, reached + 1, STEP_MS):
                for team in engine.teams:
                    window = engine.team_window(team, period, max(0, end_ms - WINDOW_MS), end_ms)
                    rows.append(team_state_row(self.match_id, window))
                self._snapshot_ms[period] = end_ms
        return rows


def rejected(results: list[ValidationResult]) -> bool:
    """True when a rule rejected the row (flags still pass)."""
    return any(r.action is RuleAction.REJECT for r in results)
