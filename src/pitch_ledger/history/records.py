"""Build a match record from a finished match's rows, with the engine's own definitions.

Team lines use the full-time recap metrics (``rule_metrics.recap``), player lines the same
counting as the stats tools (counted passes, ``shot_xg``, ``physical`` over tracking), and
goals keep the event that scored them.
"""

from collections import defaultdict
from collections.abc import Sequence

from pitch_ledger.engine import rule_metrics as rm
from pitch_ledger.engine.metrics import is_counted_pass, shot_xg
from pitch_ledger.engine.physical import physical
from pitch_ledger.engine.windows import MatchEngine
from pitch_ledger.schema import Event, Match, TrackingSample
from pitch_ledger.schema.enums import EventType, Outcome
from pitch_ledger.schema.history import GoalLine, MatchRecord, PlayerMatchLine, TeamMatchLine


class MatchNotFinished(ValueError):
    """A record needs the match's full-time event."""


def _result(goals_for: int, goals_against: int) -> str:
    return "win" if goals_for > goals_against else "loss" if goals_for < goals_against else "draw"


def build_record(
    match: Match, events: Sequence[Event], tracking: Sequence[TrackingSample] = ()
) -> MatchRecord:
    full_time = next(
        (e for e in events if e.event_type is EventType.PERIOD_END and e.period == 2), None
    )
    if full_time is None:
        raise MatchNotFinished(f"{match.match_id} has no full-time event")
    home, away = match.home_team_id, match.away_team_id
    totals, _ = rm.recap(MatchEngine(match, events), full_time, home, away)
    score = {home: int(totals["score_home"]), away: int(totals["score_away"])}
    teams = []
    for side, team, opponent in (("home", home, away), ("away", away, home)):
        accuracy = totals[f"pass_accuracy_{side}"]
        teams.append(TeamMatchLine(
            team_id=team, opponent_id=opponent, venue=side,
            goals_for=score[team], goals_against=score[opponent],
            result=_result(score[team], score[opponent]),
            shots=int(totals[f"shots_{side}"]), on_target=int(totals[f"on_target_{side}"]),
            xg=totals[f"xg_{side}"], possession_pct=totals[f"possession_{side}"],
            passes=int(totals[f"passes_{side}"]), pass_accuracy=accuracy,
        ))  # fmt: skip

    by_player: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        if e.player_id:
            by_player[e.player_id].append(e)
    running = physical(tracking)
    team_of = {e.player_id: e.team_id for e in events if e.player_id and e.team_id}
    team_of |= {s.player_id: s.player_id[:3] for s in tracking if s.player_id not in team_of}
    players = []
    for pid in sorted(set(by_player) | set(running)):
        own = by_player.get(pid, [])
        passes = [e for e in own if is_counted_pass(e)]
        shots = [e for e in own if e.event_type is EventType.SHOT]
        done = sum(e.outcome is Outcome.COMPLETE for e in passes)
        run = running.get(pid)
        players.append(PlayerMatchLine(
            player_id=pid, team_id=team_of.get(pid, pid[:3]),
            goals=sum(e.outcome is Outcome.GOAL for e in shots), shots=len(shots),
            xg=round(sum(shot_xg(e) for e in shots), 2), passes=len(passes),
            passes_completed=done,
            pass_accuracy=round(100 * done / len(passes), 1) if passes else None,
            distance_km=round(run.distance_km, 2) if run else None,
            top_speed_kmh=run.top_speed_kmh if run else None,
            sprints=run.sprints if run else None,
        ))  # fmt: skip

    goals = [
        GoalLine(event_id=e.event_id, team_id=e.team_id, player_id=e.player_id,
                 period=e.period, clock_ms=e.clock_ms)
        for e in events
        if e.event_type is EventType.SHOT and e.outcome is Outcome.GOAL and e.team_id
    ]  # fmt: skip
    tally: dict[str, int] = defaultdict(int)
    for g in goals:
        if g.player_id:
            tally[g.player_id] += 1
    return MatchRecord(
        match_id=match.match_id,
        competition=match.competition,
        season=match.season,
        kickoff_utc=match.kickoff_utc,
        home_team_id=home,
        away_team_id=away,
        score_home=score[home],
        score_away=score[away],
        teams=teams,
        players=players,
        goals=goals,
        hat_tricks=sorted(p for p, n in tally.items() if n >= 3),
        generator_version=match.generator_version,
    )
