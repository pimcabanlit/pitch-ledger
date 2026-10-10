"""Run every rule over a match. ``MatchValidator`` takes raw rows in order (live feed or file)."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from pitch_ledger.schema import Event, Match, Player, TrackingSample
from pitch_ledger.validation import row_rules
from pitch_ledger.validation.lineup import Lineup
from pitch_ledger.validation.match_rules import PeriodMarkerCounter
from pitch_ledger.validation.realism import check_realism, measure
from pitch_ledger.validation.results import ValidationReport, ValidationResult, violation
from pitch_ledger.validation.sequence_rules import SequenceChecker

RawRow = Mapping[str, Any] | Event


def _parse_error_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first["loc"]) or "row"
    return f"{where}: {first['msg']} ({exc.error_count()} error(s))"


class MatchValidator:
    """Stateful ingest validator for one match's event stream."""

    def __init__(self, match: Match, players: Iterable[Player], stateful: bool = True) -> None:
        """``stateful=False`` runs only the rules that need no earlier rows (a feed joined
        mid-match cannot know the lineup or the previous sequence number)."""
        self.match = match
        self.stateful = stateful
        self.lineup = Lineup(players)
        self.sequence = SequenceChecker(match.home_team_id, match.away_team_id)
        self.markers = PeriodMarkerCounter()
        self.accepted: list[Event] = []

    def check(self, raw: RawRow) -> tuple[Event | None, list[ValidationResult]]:
        """Validate one row. Returns the parsed event (None if it could not be parsed)."""
        if isinstance(raw, Event):
            event = raw
        else:
            try:
                event = Event.model_validate(raw)
            except ValidationError as exc:
                eid = raw.get("event_id") if isinstance(raw.get("event_id"), str) else None
                return None, [violation("ROW-01", eid, _parse_error_message(exc))]
        results = [
            *row_rules.check_required(event, self.match.match_id),
            *row_rules.check_coordinates(event),
            *row_rules.check_speeds(event),
        ]
        if self.stateful:
            results += [
                *row_rules.check_player_on_pitch(event, self.lineup),
                *self.sequence.check(event),
            ]
        self.lineup.apply(event)
        self.markers.add(event)
        if not any(r.action == "reject" for r in results):
            self.accepted.append(event)
        return event, results

    def finish(self) -> list[ValidationResult]:
        """Close the stream: pending look-ahead checks and MATCH-01."""
        if not self.stateful:
            return []
        return [*self.sequence.finish(), *self.markers.results()]


def check_tracking_rows(
    match_id: str, rows: Iterable[RawRow | TrackingSample]
) -> tuple[list[TrackingSample], list[ValidationResult]]:
    """Parse and row-check tracking samples."""
    samples: list[TrackingSample] = []
    results: list[ValidationResult] = []
    for raw in rows:
        if isinstance(raw, TrackingSample):
            sample = raw
        else:
            try:
                sample = TrackingSample.model_validate(raw)
            except ValidationError as exc:
                results.append(violation("ROW-01", None, f"tracking {_parse_error_message(exc)}"))
                continue
        results.extend(row_rules.check_tracking(sample, match_id))
        samples.append(sample)
    return samples, results


def validate_match(
    match: Match,
    players: Sequence[Player],
    events: Iterable[RawRow],
    tracking: Iterable[RawRow | TrackingSample] | None = None,
    realism: bool = True,
) -> ValidationReport:
    """Validate a whole match: every event rule, tracking rows and (optionally) realism."""
    validator = MatchValidator(match, players)
    results: list[ValidationResult] = []
    parsed: list[Event] = []
    for raw in events:
        event, found = validator.check(raw)
        results.extend(found)
        if event is not None:
            parsed.append(event)
    results.extend(validator.finish())
    samples: list[TrackingSample] = []
    if tracking is not None:
        samples, found = check_tracking_rows(match.match_id, tracking)
        results.extend(found)
    if realism:
        results.extend(check_realism(measure(match, players, parsed, samples)))
    return ValidationReport(results, events_checked=len(parsed), tracking_checked=len(samples))
