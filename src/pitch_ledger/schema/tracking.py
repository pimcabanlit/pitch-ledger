"""The ``player_tracking`` table: one row per on-pitch player per second (1 Hz)."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from pitch_ledger.schema.types import MatchId, PlayerId


class TrackingSample(BaseModel):
    """Position oriented so the player's own team attacks toward x = 105."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    match_id: MatchId
    period: Annotated[int, Field(ge=1, le=2)]
    clock_ms: Annotated[int, Field(ge=0, multiple_of=1000)]
    player_id: PlayerId
    x: float
    y: float
    speed_kmh: Annotated[float, Field(ge=0)]
