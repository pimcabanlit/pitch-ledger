"""Reference tables: match, team and player."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from pitch_ledger.schema.enums import Position, PreferredFoot, StyleProfile
from pitch_ledger.schema.types import MatchId, PlayerId, TeamId, UtcTimestamp

Rating = Annotated[int, Field(ge=1, le=99)]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Match(_Frozen):
    """One row per match."""

    match_id: MatchId
    competition: str
    season: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}$")]
    kickoff_utc: UtcTimestamp
    venue: str
    pitch_length_m: Literal[105] = 105
    pitch_width_m: Literal[68] = 68
    home_team_id: TeamId
    away_team_id: TeamId
    referee: str
    scenario_id: str
    generator_version: str
    random_seed: int


class Team(_Frozen):
    """One row per team."""

    team_id: TeamId
    name: str
    short_name: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    primary_color: Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]
    formation: Annotated[str, StringConstraints(pattern=r"^\d(-\d){2,3}$")]
    style_profile: StyleProfile


class Ratings(_Frozen):
    """Generator-only ability ratings (1-99). Never shown as facts."""

    pace: Rating
    passing: Rating
    finishing: Rating
    pressing: Rating


class Player(_Frozen):
    """One row per squad player in a match (``is_starter`` is per match)."""

    player_id: PlayerId
    team_id: TeamId
    name: str
    display_name: str
    shirt_number: Annotated[int, Field(ge=1, le=99)]
    position: Position
    preferred_foot: PreferredFoot
    is_starter: bool
    top_speed_kmh: Annotated[float, Field(gt=0, lt=40)]
    ratings: Ratings
