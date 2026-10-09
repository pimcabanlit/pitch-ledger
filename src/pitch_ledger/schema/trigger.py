"""The ``trigger`` contract: a detected moment handed to the agents (via the queue).

Every number is computed by the engine; ``evidence_event_ids`` lists the events those
numbers are computed from, and ``context`` records the windows used so a recompute can
reproduce them exactly.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from pitch_ledger.schema.enums import InsightType
from pitch_ledger.schema.insight import InsightSubject
from pitch_ledger.schema.types import EventId, MatchId

TriggerId = Annotated[str, StringConstraints(pattern=r"^M\d{4}-T\d{4}$")]


class Trigger(BaseModel):
    """A moment worth explaining, with code-computed numbers and evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_id: TriggerId
    match_id: MatchId
    period: Annotated[int, Field(ge=1, le=2)]
    clock_ms: Annotated[int, Field(ge=0)]
    insight_type: InsightType
    rule_id: str
    subject: InsightSubject
    metrics: dict[str, int | float]
    evidence_event_ids: list[EventId]
    context: dict[str, JsonValue] = Field(default_factory=dict)
    auto_approve: bool = False
