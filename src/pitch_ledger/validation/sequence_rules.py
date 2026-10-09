"""Sequence rules: checks that need earlier (or the next) events.

Written for streaming: rules that look one event ahead keep a pending check that the
next event resolves. State advances on every parsed row, so one bad row produces one
result rather than a cascade.
"""

from dataclasses import dataclass

from pitch_ledger.schema import Event, PassDetails, ShotDetails
from pitch_ledger.schema.enums import EventType, Outcome, SetPiece
from pitch_ledger.validation.results import ValidationResult, violation

ADMIN_TYPES = frozenset({EventType.CARD, EventType.SUBSTITUTION})
"""Events that may sit between a goal and the restart."""


@dataclass
class _PendingGoal:
    event_id: str
    scorer_team_id: str
    expected_score: tuple[int, int]
    score_checked: bool = False


class SequenceChecker:
    """SEQ-01 to SEQ-05 over events fed in order."""

    def __init__(self, home_team_id: str, away_team_id: str) -> None:
        self.home = home_team_id
        self.away = away_team_id
        self.seen_ids: set[str] = set()
        self.prev: Event | None = None
        self.pending_pass: Event | None = None
        self.pending_goal: _PendingGoal | None = None

    def check(self, event: Event) -> list[ValidationResult]:
        out = [
            *self._seq_and_clock(event),
            *self._pass_receipt(event),
            *self._possession(event),
            *self._goal(event),
            *self._related(event),
        ]
        self.seen_ids.add(event.event_id)
        self.prev = event
        return out

    def finish(self) -> list[ValidationResult]:
        """Resolve look-ahead checks left open by the last event."""
        out = []
        if self.pending_pass is not None:
            out.append(
                violation("SEQ-02", self.pending_pass.event_id, "completed pass has no receipt")
            )
        if self.pending_goal is not None:
            out.append(
                violation("SEQ-04", self.pending_goal.event_id, "goal not followed by a kick-off")
            )
        self.pending_pass = None
        self.pending_goal = None
        return out

    # SEQ-01
    def _seq_and_clock(self, event: Event) -> list[ValidationResult]:
        prev, eid = self.prev, event.event_id
        if prev is None:
            if event.seq_no != 1:
                return [violation("SEQ-01", eid, f"first seq_no is {event.seq_no}, expected 1")]
            return []
        out = []
        if event.seq_no != prev.seq_no + 1:
            out.append(
                violation(
                    "SEQ-01", eid, f"seq_no {event.seq_no} after {prev.seq_no}, gap or repeat"
                )
            )
        if event.period < prev.period:
            out.append(violation("SEQ-01", eid, "period goes backwards"))
        elif event.period == prev.period and event.clock_ms < prev.clock_ms:
            out.append(
                violation("SEQ-01", eid, f"clock_ms {event.clock_ms} < {prev.clock_ms} in period")
            )
        return out

    # SEQ-02
    def _pass_receipt(self, event: Event) -> list[ValidationResult]:
        out = []
        sent = self.pending_pass
        if sent is not None:
            ok = (
                event.event_type is EventType.BALL_RECEIPT
                and event.player_id == sent.receiver_player_id
                and event.team_id == sent.team_id
                and sent.event_id in event.related_event_ids
            )
            if not ok:
                msg = f"completed pass not followed by receipt by {sent.receiver_player_id}"
                out.append(violation("SEQ-02", sent.event_id, msg))
            self.pending_pass = None
        if event.event_type is EventType.PASS and event.outcome is Outcome.COMPLETE:
            if event.receiver_player_id is None:
                out.append(violation("SEQ-02", event.event_id, "completed pass has no receiver"))
            else:
                self.pending_pass = event
        return out

    # SEQ-03
    def _possession(self, event: Event) -> list[ValidationResult]:
        prev = self.prev
        if prev is None:
            if event.possession_id != 1:
                return [violation("SEQ-03", event.event_id, "first possession_id must be 1")]
            return []
        team_changed = event.possession_team_id != prev.possession_team_id
        id_changed = event.possession_id != prev.possession_id
        if id_changed and not team_changed:
            return [violation("SEQ-03", event.event_id, "possession_id changed, team did not")]
        if team_changed and event.possession_id != prev.possession_id + 1:
            msg = f"possession team changed; possession_id should be {prev.possession_id + 1}"
            return [violation("SEQ-03", event.event_id, msg)]
        return []

    # SEQ-04
    def _goal(self, event: Event) -> list[ValidationResult]:
        out = []
        prev, pending = self.prev, self.pending_goal
        score = (event.score_home, event.score_away)
        if pending is not None and not pending.score_checked:
            pending.score_checked = True
            if score != pending.expected_score:
                msg = f"score {score} after goal, expected {pending.expected_score}"
                out.append(violation("SEQ-04", event.event_id, msg))
        elif prev is not None and score != (prev.score_home, prev.score_away):
            out.append(violation("SEQ-04", event.event_id, "score changed without a goal"))
        if pending is not None and event.event_type not in ADMIN_TYPES:
            if not self._is_restart_after(event, pending):
                msg = "goal not followed by a kick-off by the conceding team"
                out.append(violation("SEQ-04", pending.event_id, msg))
            self.pending_goal = None
        is_goal = event.event_type is EventType.SHOT and event.outcome is Outcome.GOAL
        if is_goal and event.team_id is not None:
            home_scored = event.team_id == self.home
            expected = (
                event.score_home + int(home_scored),
                event.score_away + int(not home_scored),
            )
            self.pending_goal = _PendingGoal(event.event_id, event.team_id, expected)
        return out

    def _is_restart_after(self, event: Event, goal: _PendingGoal) -> bool:
        if event.event_type is EventType.PERIOD_END:
            return True
        return (
            event.event_type is EventType.PASS
            and isinstance(event.details, PassDetails)
            and event.details.set_piece is SetPiece.KICK_OFF
            and event.team_id is not None
            and event.team_id != goal.scorer_team_id
        )

    # SEQ-05
    def _related(self, event: Event) -> list[ValidationResult]:
        refs = list(event.related_event_ids)
        if isinstance(event.details, ShotDetails) and event.details.assist_event_id:
            refs.append(event.details.assist_event_id)
        return [
            violation("SEQ-05", event.event_id, f"{ref} does not exist yet")
            for ref in refs
            if ref not in self.seen_ids
        ]
