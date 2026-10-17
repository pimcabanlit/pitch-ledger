"""The ``story`` record: the "Tell me more" beats for one aired insight, kept in the ledger.

One record per insight. Each beat set is one depth (``plain`` for every mode but the
analyst, ``numbers`` for the analyst) in one language: the buildup (what led to the moment)
and the outlook (what it could mean). The middle beat, what just happened, is the aired
overlay for the reader's mode and language, so it is not stored twice.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pitch_ledger.schema.types import Bcp47, InsightId, MatchId

StoryDepth = Literal["plain", "numbers"]
BeatSource = Literal["model", "template"]


class StoryBeats(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    depth: StoryDepth
    language: Bcp47
    buildup: str
    outlook: str
    sources: dict[str, BeatSource] = Field(default_factory=dict)
    """Who wrote each beat: the Narrator or Translator (``model``), or the template."""


class StoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    insight_id: InsightId
    match_id: MatchId
    beats: list[StoryBeats]
    model_version: str
    written_at: datetime | None = None

    def beats_for(self, depth: str, language: str) -> StoryBeats | None:
        return next((b for b in self.beats if b.depth == depth and b.language == language), None)
