"""The fixed fictional league: clubs, squads, style profiles and ratings.

Data lives in ``matchlab/data/league.json``, generated once and committed so names never
change. Every club, player, venue and referee is fictional.
"""

from functools import cache
from importlib import resources
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from pitch_ledger.schema import Player, Ratings, Team
from pitch_ledger.schema.enums import Position, PreferredFoot, StyleProfile
from pitch_ledger.schema.types import PlayerId, TeamId

MATCHDAY_STARTERS = 11
MATCHDAY_BENCH = 9


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RosterPlayer(_Frozen):
    """A squad player; ``is_starter`` is decided per match, so it is not stored here."""

    player_id: PlayerId
    team_id: TeamId
    name: str
    display_name: str
    shirt_number: int
    position: Position
    preferred_foot: PreferredFoot
    top_speed_kmh: float
    ratings: Ratings


class Club(_Frozen):
    """A club with its squad, home venue and default match-day selection.

    ``lineup`` lists starting shirt numbers in formation-slot order (goalkeeper, back line
    right to left, midfield, attack); ``bench`` lists the nine substitutes.
    """

    team_id: TeamId
    name: str
    short_name: str
    primary_color: str
    formation: str
    style_profile: StyleProfile
    venue: str
    lineup: Annotated[list[int], Field(min_length=11, max_length=11)]
    bench: Annotated[list[int], Field(min_length=MATCHDAY_BENCH, max_length=MATCHDAY_BENCH)]
    players: list[RosterPlayer]

    def team(self) -> Team:
        """The ``team`` table row."""
        return Team(
            team_id=self.team_id,
            name=self.name,
            short_name=self.short_name,
            primary_color=self.primary_color,
            formation=self.formation,
            style_profile=self.style_profile,
        )

    def player(self, shirt_number: int) -> RosterPlayer:
        return next(p for p in self.players if p.shirt_number == shirt_number)

    def matchday_squad(self) -> list[Player]:
        """The 20-player ``player`` table rows for a match: starters first, in slot order."""
        out = []
        for shirt in [*self.lineup, *self.bench]:
            data = self.player(shirt).model_dump()
            out.append(Player.model_validate({**data, "is_starter": shirt in self.lineup}))
        return out


class League(_Frozen):
    competition: str
    season: str
    referees: list[str]
    teams: list[Club]

    def club(self, team_id: str) -> Club:
        for club in self.teams:
            if club.team_id == team_id:
                return club
        raise KeyError(f"unknown team {team_id}")


@cache
def load_league() -> League:
    """Load the committed league file (cached)."""
    text = resources.files("pitch_ledger.matchlab").joinpath("data/league.json").read_text("utf-8")
    return League.model_validate_json(text)
