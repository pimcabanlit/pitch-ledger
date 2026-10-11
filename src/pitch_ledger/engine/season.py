"""Season facts for insights, computed from earlier matches' records (docs/event-schema.md,
"Season facts").

A match sees only records of the same competition and season that kicked off before it,
so its season facts never change once the match is played, live or in a replay. A trigger
that uses them lists those matches in ``context["season"]``; the Auditor recomputes the same
metrics from the same records and refuses if the records it can see differ.

- On a goal: ``team_matches_before`` (the scorer's club's earlier league matches this
  season), ``season_goals`` (the scorer's league goals this season, this one included) and,
  on a hat-trick, ``season_hat_tricks_before`` (hat-tricks earlier in the league season).
- On the full-time recap: ``{side}_form_played``, ``_wins``, ``_draws``, ``_losses`` (the
  club's last league matches, up to 5, this one included), and ``h2h_home_goals`` /
  ``h2h_away_goals`` (the latest earlier meeting this season, as goals by today's home and
  away clubs).

Facts appear only when the club has played earlier this season; on the opening day there
is nothing to add.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pitch_ledger.schema import Event, Match
from pitch_ledger.schema.history import MatchRecord

Metrics = dict[str, int | float]
FORM_MATCHES = 5
SEASON_METRICS = frozenset({
    "team_matches_before", "season_goals", "season_hat_tricks_before",
    *(f"{side}_form_{k}" for side in ("home", "away")
      for k in ("played", "wins", "draws", "losses")),
    "h2h_home_goals", "h2h_away_goals",
})  # fmt: skip
"""Metrics that come from earlier matches (copy may then speak of the season)."""


def has_season_facts(metrics: Iterable[str]) -> bool:
    return any(k in SEASON_METRICS for k in metrics)


@dataclass(frozen=True)
class SeasonView:
    """A match's view of its season: earlier records only, in kick-off order."""

    competition: str
    season: str
    records: tuple[MatchRecord, ...]

    @classmethod
    def before(cls, match: Match, records: Iterable[MatchRecord]) -> "SeasonView":
        earlier = [
            r for r in records
            if (r.competition, r.season) == (match.competition, match.season)
            and r.kickoff_utc < match.kickoff_utc and r.match_id != match.match_id
        ]  # fmt: skip
        earlier.sort(key=lambda r: (r.kickoff_utc, r.match_id))
        return cls(match.competition, match.season, tuple(earlier))

    @property
    def match_ids(self) -> list[str]:
        return [r.match_id for r in self.records]

    def context(self) -> dict[str, Any]:
        """What a trigger records about the season data it used."""
        return {"competition": self.competition, "season": self.season, "matches": self.match_ids}

    def of_team(self, team_id: str) -> list[MatchRecord]:
        return [r for r in self.records if team_id in (r.home_team_id, r.away_team_id)]


def goal_metrics(view: SeasonView, shot: Event, player_goals: int) -> Metrics:
    """The scorer's season so far (``player_goals``: their goals in this match, this one
    included)."""
    if shot.team_id is None or shot.player_id is None:
        return {}
    earlier = view.of_team(shot.team_id)
    if not earlier:
        return {}
    metrics: Metrics = {
        "team_matches_before": len(earlier),
        "season_goals": sum(r.player_goals(shot.player_id) for r in earlier) + player_goals,
    }
    if player_goals == 3:
        metrics["season_hat_tricks_before"] = sum(len(r.hat_tricks) for r in view.records)
    return metrics


def _result(mine: int, theirs: int) -> str:
    return "wins" if mine > theirs else "losses" if mine < theirs else "draws"


def recap_metrics(view: SeasonView, match: Match, score_home: int, score_away: int) -> Metrics:
    """Full time: each club's recent league form (this match included) and the latest
    earlier meeting this season."""
    metrics: Metrics = {}
    today = {match.home_team_id: (score_home, score_away),
             match.away_team_id: (score_away, score_home)}  # fmt: skip
    for side, team in (("home", match.home_team_id), ("away", match.away_team_id)):
        earlier = view.of_team(team)
        if not earlier:
            continue
        lines = [r.team(team) for r in earlier][-(FORM_MATCHES - 1) :]
        results = [_result(t.goals_for, t.goals_against) for t in lines if t is not None]
        results.append(_result(*today[team]))
        metrics[f"{side}_form_played"] = len(results)
        for k in ("wins", "draws", "losses"):
            metrics[f"{side}_form_{k}"] = results.count(k)
    pair = {match.home_team_id, match.away_team_id}
    meetings = [r for r in view.records if {r.home_team_id, r.away_team_id} == pair]
    if meetings:
        last = meetings[-1]
        home_line = last.team(match.home_team_id)
        if home_line is not None:
            metrics["h2h_home_goals"] = home_line.goals_for
            metrics["h2h_away_goals"] = home_line.goals_against
    return metrics
