"""The league table so far, from match records (3 points a win, 1 a draw)."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from pitch_ledger.schema.history import MatchRecord


@dataclass
class TableRow:
    team_id: str
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0
    form: list[str] = field(default_factory=list)  # W/D/L, oldest first

    @property
    def points(self) -> int:
        return 3 * self.won + self.drawn

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


def league_table(records: Sequence[MatchRecord]) -> list[TableRow]:
    """Rows ordered by points, goal difference, goals scored, then team ID."""
    rows: dict[str, TableRow] = {}
    for record in sorted(records, key=lambda r: (r.kickoff_utc, r.match_id)):
        for line in record.teams:
            row = rows.setdefault(line.team_id, TableRow(line.team_id))
            row.played += 1
            row.goals_for += line.goals_for
            row.goals_against += line.goals_against
            if line.result == "win":
                row.won += 1
            elif line.result == "draw":
                row.drawn += 1
            else:
                row.lost += 1
            row.form.append(line.result[0].upper())
    return sorted(
        rows.values(), key=lambda r: (-r.points, -r.goal_difference, -r.goals_for, r.team_id)
    )
