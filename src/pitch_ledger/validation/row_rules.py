"""Row rules: each checks one event (or tracking sample) on its own, plus lineup state."""

from pitch_ledger.core.formulas import ball_speed_kmh, distance
from pitch_ledger.core.pitch import in_bounds
from pitch_ledger.schema import Event, ShotDetails, SubstitutionDetails, TrackingSample
from pitch_ledger.schema.enums import (
    ALLOWED_OUTCOMES,
    NO_ACTOR_TYPES,
    NO_LOCATION_TYPES,
    EventType,
)
from pitch_ledger.schema.types import event_id as build_event_id
from pitch_ledger.validation.lineup import Lineup
from pitch_ledger.validation.results import ValidationResult, violation

SHOT_SPEED_KMH = (30.0, 140.0)
MAX_BALL_SPEED_KMH = 130.0
MAX_TRACKING_SPEED_KMH = 37.0


def check_required(event: Event, match_id: str) -> list[ValidationResult]:
    """ROW-01 cross-field part: IDs, outcome for type, actor and location presence."""
    eid = event.event_id
    problems: list[str] = []
    if event.match_id != match_id:
        problems.append(f"match_id {event.match_id} is not {match_id}")
    if event.event_id != build_event_id(event.match_id, event.seq_no):
        problems.append("event_id does not match match_id and seq_no")
    allowed = ALLOWED_OUTCOMES[event.event_type]
    if allowed and event.outcome not in allowed:
        problems.append(f"outcome {event.outcome} not valid for {event.event_type}")
    if not allowed and event.outcome is not None:
        problems.append(f"{event.event_type} takes no outcome")
    has_actor = event.event_type not in NO_ACTOR_TYPES
    if has_actor and (event.team_id is None or event.player_id is None):
        problems.append(f"{event.event_type} needs team_id and player_id")
    if not has_actor and (event.team_id is not None or event.player_id is not None):
        problems.append(f"{event.event_type} has no actor")
    coords = (event.start_x, event.start_y, event.end_x, event.end_y)
    if event.event_type in NO_LOCATION_TYPES:
        if any(c is not None for c in coords):
            problems.append(f"{event.event_type} has no coordinates")
    elif any(c is None for c in coords):
        problems.append(f"{event.event_type} needs start and end coordinates")
    if event.receiver_player_id is not None and event.event_type is not EventType.PASS:
        problems.append("receiver_player_id is for passes only")
    return [violation("ROW-01", eid, p) for p in problems]


def check_coordinates(event: Event) -> list[ValidationResult]:
    """ROW-02: start and end points lie on the pitch."""
    out = []
    for label, x, y in (
        ("start", event.start_x, event.start_y),
        ("end", event.end_x, event.end_y),
    ):
        if x is not None and y is not None and not in_bounds(x, y):
            out.append(violation("ROW-02", event.event_id, f"{label} ({x}, {y}) is off the pitch"))
    return out


def check_player_on_pitch(event: Event, lineup: Lineup) -> list[ValidationResult]:
    """ROW-03: the actor belongs to the acting team and is on the pitch."""
    if event.player_id is None:
        return []
    eid, pid = event.event_id, event.player_id
    team = lineup.team_of.get(pid)
    if team is None:
        return [violation("ROW-03", eid, f"{pid} is not in the match squad")]
    if team != event.team_id:
        return [violation("ROW-03", eid, f"{pid} does not belong to {event.team_id}")]
    out = []
    if pid not in lineup.on_pitch:
        out.append(violation("ROW-03", eid, f"{pid} is not on the pitch"))
    if isinstance(event.details, SubstitutionDetails):
        sub_in, sub_out = event.details.player_in_id, event.details.player_out_id
        if sub_out != pid:
            out.append(violation("ROW-03", eid, "player_out_id must be the acting player"))
        if lineup.team_of.get(sub_in) != event.team_id:
            out.append(violation("ROW-03", eid, f"{sub_in} does not belong to {event.team_id}"))
        elif sub_in in lineup.used:
            out.append(violation("ROW-03", eid, f"{sub_in} has already played"))
    return out


def check_speeds(event: Event) -> list[ValidationResult]:
    """ROW-04 (events): shot speed 30-140 km/h; pass ball speed below 130 km/h."""
    if isinstance(event.details, ShotDetails):
        low, high = SHOT_SPEED_KMH
        speed = event.details.shot_speed_kmh
        if not low <= speed <= high:
            return [violation("ROW-04", event.event_id, f"shot speed {speed} km/h outside 30-140")]
    if (
        event.event_type is EventType.PASS
        and event.duration_ms
        and event.start_x is not None
        and event.start_y is not None
        and event.end_x is not None
        and event.end_y is not None
    ):
        length = distance(event.start_x, event.start_y, event.end_x, event.end_y)
        speed = ball_speed_kmh(length, event.duration_ms)
        if speed >= MAX_BALL_SPEED_KMH:
            return [violation("ROW-04", event.event_id, f"ball speed {speed:.1f} km/h >= 130")]
    return []


def check_tracking(sample: TrackingSample, match_id: str) -> list[ValidationResult]:
    """Row rules for tracking samples: ROW-01 match ID, ROW-02 bounds, ROW-04 speed."""
    ref = f"{sample.player_id}@{sample.period}:{sample.clock_ms}"
    out = []
    if sample.match_id != match_id:
        out.append(violation("ROW-01", None, f"tracking {ref}: match_id is not {match_id}"))
    if not in_bounds(sample.x, sample.y):
        out.append(violation("ROW-02", None, f"tracking {ref}: ({sample.x}, {sample.y}) off pitch"))
    if sample.speed_kmh >= MAX_TRACKING_SPEED_KMH:
        out.append(violation("ROW-04", None, f"tracking {ref}: {sample.speed_kmh} km/h >= 37"))
    return out
