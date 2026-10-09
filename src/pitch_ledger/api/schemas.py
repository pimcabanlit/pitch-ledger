"""Request and response models for ``/api/v1``. The frontend gets these, never raw rows.

Fan-facing responses carry only what a viewer needs (team and player names, aired
overlays); producer responses add the audit trail and evidence.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pitch_ledger.schema import AuditRecord, Insight, Overlay

InsightStatus = Literal["aired", "pending", "blocked"]


class TeamView(BaseModel):
    team_id: str
    name: str
    short_name: str
    primary_color: str


class PlayerView(BaseModel):
    player_id: str
    team_id: str
    display_name: str
    shirt_number: int
    position: str


class MatchSummary(BaseModel):
    match_id: str
    kickoff_utc: datetime
    venue: str
    home: TeamView
    away: TeamView


class MatchDetail(MatchSummary):
    players: list[PlayerView]
    score_home: int | None
    score_away: int | None
    last_minute: float | None


class InsightView(BaseModel):
    insight: Insight
    status: InsightStatus


class EvidenceEvent(BaseModel):
    event_id: str
    minute: float
    event_type: str
    team_id: str | None
    player_id: str | None
    outcome: str | None


class InsightDetail(InsightView):
    audits: list[AuditRecord]
    evidence: list[EvidenceEvent]
    overlays: list[Overlay]


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    reason: Annotated[str, Field(max_length=500)] | None = None


class DecisionResult(BaseModel):
    insight_id: str
    status: InsightStatus
    audit_status: str | None
    overlays: int
    approved_by: str | None


class Me(BaseModel):
    producer_id: str
    name: str
    auth: str


class Realtime(BaseModel):
    url: str | None  # None: push is off, poll instead
    group: str


class Health(BaseModel):
    status: Literal["ok"]
    ledger: str
    matches: int


def team_view(team: Any) -> TeamView:
    return TeamView(team_id=team.team_id, name=team.name, short_name=team.short_name,
                    primary_color=team.primary_color)  # fmt: skip
