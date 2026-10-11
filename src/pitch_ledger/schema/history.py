"""Season history: one record per finished match (docs/event-schema.md, "Match records").

A record is computed from the match's own rows by the engine (never typed in, never from a
model) and keyed by competition, season and kick-off, so later matches can look back on the
season so far. Recomputing a record from the same match files gives the same record.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from pitch_ledger.schema.types import EventId, MatchId, PlayerId, TeamId, UtcTimestamp

RECORD_VERSION = 1


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TeamMatchLine(_Frozen):
    """One team's match: result and totals."""

    team_id: TeamId
    opponent_id: TeamId
    venue: Literal["home", "away"]
    goals_for: int
    goals_against: int
    result: Literal["win", "draw", "loss"]
    shots: int
    on_target: int
    xg: float
    possession_pct: float
    passes: int
    pass_accuracy: float | None


class PlayerMatchLine(_Frozen):
    """One player's match totals (players who touched the ball or were tracked)."""

    player_id: PlayerId
    team_id: TeamId
    goals: int
    shots: int
    xg: float
    passes: int
    passes_completed: int
    pass_accuracy: float | None
    distance_km: float | None
    top_speed_kmh: float | None
    sprints: int | None


class GoalLine(_Frozen):
    """A goal, with the event that scored it (the evidence for season tallies)."""

    event_id: EventId
    team_id: TeamId
    player_id: PlayerId | None
    period: int
    clock_ms: int


class MatchRecord(_Frozen):
    match_id: MatchId
    competition: str
    season: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}$")]
    kickoff_utc: UtcTimestamp
    home_team_id: TeamId
    away_team_id: TeamId
    score_home: int
    score_away: int
    teams: list[TeamMatchLine]
    players: list[PlayerMatchLine]
    goals: list[GoalLine]
    hat_tricks: list[PlayerId] = Field(default_factory=list)  # players with 3+ goals
    generator_version: str
    record_version: int = RECORD_VERSION

    def team(self, team_id: str) -> TeamMatchLine | None:
        return next((t for t in self.teams if t.team_id == team_id), None)

    def player_goals(self, player_id: str) -> int:
        return sum(g.player_id == player_id for g in self.goals)
