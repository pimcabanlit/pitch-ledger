"""The ``insight`` output contract (written by agents, checked by the Auditor)."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from pitch_ledger.schema.enums import AuditStatus, InsightType
from pitch_ledger.schema.types import EventId, InsightId, MatchId, PlayerId, TeamId


class InsightSubject(BaseModel):
    """Who the insight is about: a team, a player, or both."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    team_id: TeamId | None = None
    player_id: PlayerId | None = None


class Insight(BaseModel):
    """An explained, evidence-backed statement. ``metrics`` holds every number in the text."""

    model_config = ConfigDict(extra="forbid")

    insight_id: InsightId
    match_id: MatchId
    period: Annotated[int, Field(ge=1, le=2)]
    clock_ms: Annotated[int, Field(ge=0)]
    insight_type: InsightType
    subject: InsightSubject
    headline: str
    explanation: str
    metrics: dict[str, int | float]
    evidence_event_ids: list[EventId]
    confidence: Annotated[float, Field(ge=0, le=1)]
    audit_status: AuditStatus = AuditStatus.PENDING
    audit_notes: str | None = None
    approved_by: str | None = None
    created_by_agent: str
    model_version: str
