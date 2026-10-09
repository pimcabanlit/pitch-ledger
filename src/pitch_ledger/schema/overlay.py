"""The ``overlay`` output contract: one per audience mode and language."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from pitch_ledger.schema.enums import AudienceMode, OverlayTemplate
from pitch_ledger.schema.types import Bcp47, InsightId, OverlayId


class Overlay(BaseModel):
    """A rendered on-screen element tied to one insight."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    overlay_id: OverlayId
    insight_id: InsightId
    period: Annotated[int, Field(ge=1, le=2)]
    trigger_clock_ms: Annotated[int, Field(ge=0)]
    display_ms: Annotated[int, Field(gt=0)]
    priority: Annotated[int, Field(ge=1)]
    template: OverlayTemplate
    audience_mode: AudienceMode
    language: Bcp47
    text: str
    data: dict[str, JsonValue]
