"""Type-specific ``details`` objects, one model per event type."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from pitch_ledger.schema.enums import (
    CardReason,
    EventType,
    GoalkeeperAction,
    PassHeight,
    PassType,
    SetPiece,
    ShotType,
)
from pitch_ledger.schema.types import EventId, PlayerId


class _Details(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoDetails(_Details):
    """Event types without type-specific fields carry an empty object."""


class PassDetails(_Details):
    pass_type: PassType
    height: PassHeight
    set_piece: SetPiece


class ShotDetails(_Details):
    shot_type: ShotType
    shot_speed_kmh: Annotated[float, Field(gt=0)]
    on_target: bool
    goalkeeper_id: PlayerId | None = None
    assist_event_id: EventId | None = None
    assist_type: PassType | None = None


class PressureDetails(_Details):
    target_player_id: PlayerId
    counterpress: bool


class DuelDetails(_Details):
    """Tackle and interception: the player who lost the ball."""

    opponent_player_id: PlayerId


class FoulDetails(_Details):
    fouled_player_id: PlayerId


class CardDetails(_Details):
    reason: CardReason


class SubstitutionDetails(_Details):
    player_in_id: PlayerId
    player_out_id: PlayerId


class GoalkeeperDetails(_Details):
    action: GoalkeeperAction


EventDetails = (
    PassDetails
    | ShotDetails
    | PressureDetails
    | DuelDetails
    | FoulDetails
    | CardDetails
    | SubstitutionDetails
    | GoalkeeperDetails
    | NoDetails
)

DETAILS_MODEL: dict[EventType, type[_Details]] = {
    EventType.PASS: PassDetails,
    EventType.SHOT: ShotDetails,
    EventType.PRESSURE: PressureDetails,
    EventType.TACKLE: DuelDetails,
    EventType.INTERCEPTION: DuelDetails,
    EventType.FOUL: FoulDetails,
    EventType.CARD: CardDetails,
    EventType.SUBSTITUTION: SubstitutionDetails,
    EventType.GOALKEEPER: GoalkeeperDetails,
}


def details_model_for(event_type: EventType) -> type[_Details]:
    """Return the details model for an event type (``NoDetails`` if it has none)."""
    return DETAILS_MODEL.get(event_type, NoDetails)
