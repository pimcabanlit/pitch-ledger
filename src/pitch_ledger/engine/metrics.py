"""Per-event derived metrics (docs/event-schema.md, "Derived metrics").

Every number shown on screen is computed here, or in the window and physical modules,
from event rows; nothing comes from a language model.
"""

from pitch_ledger.core.formulas import (
    ball_speed_kmh,
    distance,
    expected_goals,
    forward_progress,
    pass_difficulty,
)
from pitch_ledger.schema import Event, PassDetails, ShotDetails
from pitch_ledger.schema.enums import EventType, PassHeight, PassType, SetPiece, ShotType

SHOT_SPEED_ALERT_KMH = 100.0


def pass_length_m(event: Event) -> float:
    """Pass distance (m) between stored start and end."""
    _require_pass(event)
    assert event.start_x is not None and event.start_y is not None
    assert event.end_x is not None and event.end_y is not None
    return distance(event.start_x, event.start_y, event.end_x, event.end_y)


def pass_forward_m(event: Event) -> float:
    """Forward progress (m) = max(0, end_x - start_x)."""
    _require_pass(event)
    assert event.start_x is not None and event.end_x is not None
    return forward_progress(event.start_x, event.end_x)


def pass_ball_speed_kmh(event: Event) -> float | None:
    """Ball speed (km/h) of a pass, or None without a duration."""
    if not event.duration_ms:
        return None
    return ball_speed_kmh(pass_length_m(event), event.duration_ms)


def pass_difficulty_of(event: Event) -> float:
    """Pass difficulty (0-100) from the stored row, the same model the Match Lab used."""
    details = _require_pass(event)
    return pass_difficulty(
        pass_length_m(event),
        event.under_pressure,
        pass_forward_m(event),
        details.height is PassHeight.HIGH,
        details.pass_type in (PassType.THROUGH_BALL, PassType.CROSS),
    )


def shot_xg(event: Event) -> float:
    """Expected goals of a shot from its row (penalties fixed at 0.78)."""
    if event.event_type is not EventType.SHOT or not isinstance(event.details, ShotDetails):
        raise ValueError(f"{event.event_id} is not a shot")
    assert event.start_x is not None and event.start_y is not None
    d = event.details
    return expected_goals(
        event.start_x,
        event.start_y,
        header=d.shot_type is ShotType.HEADER,
        through_ball_assist=d.assist_type is PassType.THROUGH_BALL,
        penalty=d.shot_type is ShotType.PENALTY,
    )


def shot_speed_kmh(event: Event) -> float:
    """Generated peak ball speed of a shot (``details.shot_speed_kmh``)."""
    if not isinstance(event.details, ShotDetails):
        raise ValueError(f"{event.event_id} is not a shot")
    return event.details.shot_speed_kmh


def is_counted_pass(event: Event) -> bool:
    """A pass that counts toward pass totals and accuracy (throw-ins excluded)."""
    return (
        event.event_type is EventType.PASS
        and isinstance(event.details, PassDetails)
        and event.details.set_piece is not SetPiece.THROW_IN
    )


def _require_pass(event: Event) -> PassDetails:
    if event.event_type is not EventType.PASS or not isinstance(event.details, PassDetails):
        raise ValueError(f"{event.event_id} is not a pass")
    return event.details
