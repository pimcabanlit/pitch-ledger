"""Request and response models for ``/api/v1``. The frontend gets these, never raw rows.

Fan-facing responses carry only what a viewer needs (team and player names, aired
overlays); producer responses add the audit trail and evidence.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pitch_ledger.schema import AuditRecord, Insight, OpsAlert, Overlay, StoryRecord

InsightStatus = Literal["aired", "pending", "blocked"]
AlertState = Literal["open", "all"]


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
    score_home: int | None = None  # after the last event (a goal counts at the shot)
    score_away: int | None = None
    aired_insights: int | None = None  # in the match list only


class MatchDetail(MatchSummary):
    players: list[PlayerView]
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


class AuditStep(AuditRecord):
    summary: str  # the step in plain words (api.plain.explain_step)


class InsightDetail(InsightView):
    audits: list[AuditStep]
    evidence: list[EvidenceEvent]
    overlays: list[Overlay]
    process: "InsightProcess | None" = None  # the process flow (api.story.build_process)


class AlertView(OpsAlert):
    """An ops alert with plain words for the producer (api.plain.explain_alert); ``message``
    stays the technical text."""

    headline: str
    action: str
    needs_action: bool
    details: list[str]


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


StageKey = Literal["detected", "analyzed", "explained", "audited", "approved", "published"]
StepRole = Literal["agent", "test", "producer"]


class StoryStage(BaseModel):
    """One step of the pipeline, with a detail line derived from the ledger."""

    key: StageKey
    label: str
    done: bool
    detail: str
    at: datetime | None = None  # wall clock, when the ledger recorded it


class StoryStep(BaseModel):
    """One audit-trail entry, public form: producers appear as "producer"."""

    audit_id: str
    round: int
    agent: str
    role: StepRole
    action: str
    passed: bool | None
    findings: list[str]
    headline: str | None
    explanation: str | None
    unsupported_numbers: list[str]  # numbers in the draft that no computed metric supports
    recorded_at: datetime | None


class StoryWindow(BaseModel):
    label: str
    start_minute: float
    end_minute: float


class StoryTrigger(BaseModel):
    trigger_id: str
    rule_id: str
    minute: float
    detector: str | None
    metrics: dict[str, int | float]
    windows: list[StoryWindow]
    season_matches: list[str] = []  # earlier matches this season behind any season facts


class InsightProcess(BaseModel):
    """The pipeline behind one insight, stage by stage, for the producer console."""

    trigger: StoryTrigger | None
    stages: list[StoryStage]
    steps: list[StoryStep]
    evidence_counts: dict[str, int]
    story: StoryRecord | None = None
    """The "Tell me more" beats as stored (none: the story page uses template beats)."""


class StoryNarrative(BaseModel):
    """The fan story of an aired insight for one audience mode and language (api.narrative)."""

    mode: str
    language: str
    title: str
    buildup: str  # what led to the moment
    moment: str  # what just happened: the aired on-screen version
    outlook: str  # what it could mean


class InsightStory(BaseModel):
    """An aired insight traced end to end: detection, numbers, agents, approval, overlays."""

    match: MatchSummary
    insight: Insight  # approved_by is "producer" or the auto-approval rule, never an identity
    status: InsightStatus
    minute: float
    trigger: StoryTrigger | None
    stages: list[StoryStage]
    steps: list[StoryStep]
    evidence: list[EvidenceEvent]
    evidence_counts: dict[str, int]
    overlays: list[Overlay]
    narratives: list[StoryNarrative] = []


class BroadcastGraphic(BaseModel):
    mode: str
    language: str
    template: str
    display_ms: int
    priority: int
    text: str
    data: dict[str, Any]


class BroadcastAudit(BaseModel):
    status: str
    drafts: int
    revisions: int
    approval: str | None
    model_version: str


class BroadcastPackage(BaseModel):
    """One aired insight as a structured broadcast event (a graphics system would read this)."""

    format: Literal["pitch-ledger.broadcast/1"] = "pitch-ledger.broadcast/1"
    insight_id: str
    match_id: str
    period: int
    clock_ms: int
    match_minute: float
    insight_type: str
    team_id: str | None
    player_id: str | None
    headline: str
    metrics: dict[str, int | float]
    evidence_event_ids: list[str]
    graphics: list[BroadcastGraphic]
    audit: BroadcastAudit


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
