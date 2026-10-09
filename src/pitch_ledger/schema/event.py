"""The event table: core fields, context fields and typed details (one row per event)."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pitch_ledger.schema.details import EventDetails, NoDetails, details_model_for
from pitch_ledger.schema.enums import BodyPart, EventType, Outcome, PlayPattern
from pitch_ledger.schema.types import EventId, MatchId, PlayerId, TeamId, UtcTimestamp

NonNegInt = Annotated[int, Field(ge=0)]


class Event(BaseModel):
    """One on-pitch event. Coordinates are stored with the acting team attacking toward x = 105.

    Field types and enums are enforced here; cross-field rules (outcome valid for the
    type, actor present, coordinates on the pitch) live in ``pitch_ledger.validation``
    so each failure maps to a named rule.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Core fields
    event_id: EventId
    match_id: MatchId
    seq_no: Annotated[int, Field(ge=1)]
    period: Annotated[int, Field(ge=1, le=2)]
    clock_ms: NonNegInt
    feed_ts_utc: UtcTimestamp
    team_id: TeamId | None = None
    player_id: PlayerId | None = None
    event_type: EventType
    outcome: Outcome | None = None
    start_x: float | None = None
    start_y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    body_part: BodyPart | None = None
    duration_ms: NonNegInt | None = None

    # Context fields
    possession_id: Annotated[int, Field(ge=1)]
    possession_team_id: TeamId
    play_pattern: PlayPattern
    under_pressure: bool = False
    nearest_opponent_m: Annotated[float, Field(ge=0)] | None = None
    receiver_player_id: PlayerId | None = None
    related_event_ids: list[EventId] = Field(default_factory=list)
    score_home: NonNegInt
    score_away: NonNegInt
    players_on_pitch_home: Annotated[int, Field(ge=0, le=11)]
    players_on_pitch_away: Annotated[int, Field(ge=0, le=11)]
    details: EventDetails = Field(default_factory=NoDetails)

    @model_validator(mode="before")
    @classmethod
    def _typed_details(cls, data: Any) -> Any:
        """Validate ``details`` against the model chosen by ``event_type``."""
        if not isinstance(data, dict) or "event_type" not in data:
            return data
        try:
            model = details_model_for(EventType(data["event_type"]))
        except ValueError:
            return data  # the enum error is reported by field validation
        raw = data.get("details")
        if raw is None:
            raw = {}
        if isinstance(raw, BaseModel):
            if type(raw) is model:
                return data
            raw = raw.model_dump()
        return {**data, "details": model.model_validate(raw)}
