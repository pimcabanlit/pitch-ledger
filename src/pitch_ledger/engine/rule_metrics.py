"""The metrics each detector rule reports, as plain functions of source rows.

The detector calls these when it fires; ``engine.recompute`` calls them again from the
raw events (and tracking) to verify a trigger, which is what the Auditor relies on.
Numbers are rounded as shown on screen.
"""

from collections.abc import Sequence

from pitch_ledger.engine.metrics import (
    is_counted_pass,
    pass_ball_speed_kmh,
    pass_difficulty_of,
    pass_forward_m,
    pass_length_m,
    shot_speed_kmh,
    shot_xg,
)
from pitch_ledger.engine.physical import physical
from pitch_ledger.engine.windows import MatchEngine, match_minute
from pitch_ledger.schema import Event, TrackingSample
from pitch_ledger.schema.enums import EventType, Outcome, PlayPattern

MIN = 60_000
Metrics = dict[str, int | float]
REGAIN_TO_SHOT_MS = 15_000
"""A goal counts as scored from a regain when the ball was won this recently."""
REGAINS = frozenset({EventType.BALL_RECOVERY, EventType.INTERCEPTION, EventType.TACKLE})
REGULATION_MIN = 90


def _upto(events: Sequence[Event], period: int, clock_ms: int) -> list[Event]:
    return [e for e in events if (e.period, e.clock_ms) <= (period, clock_ms)]


def _shots(events: Sequence[Event], team: str | None = None) -> list[Event]:
    return [
        e for e in events if e.event_type is EventType.SHOT and (team is None or e.team_id == team)
    ]


def _xg(shots: Sequence[Event]) -> float:
    return round(sum(shot_xg(s) for s in shots), 2)


def pct(value: float | None) -> float:
    return round(100.0 * value, 1) if value is not None else 0.0


def minute(period: int, clock_ms: int) -> float:
    return round(match_minute(period, clock_ms), 1)


def team_hsr_per_min(
    tracking: Sequence[TrackingSample], team: str, period: int, start_ms: int, end_ms: int
) -> float:
    """High-speed running (m per minute, summed over the team's players)."""
    stats = physical(
        (s for s in tracking if s.player_id.startswith(team)), period, start_ms, end_ms
    )
    span = max((end_ms - start_ms) / MIN, 1e-9)
    return round(sum(p.hsr_m + p.sprint_m for p in stats.values()) / span, 1)


def momentum(
    engine: MatchEngine,
    team: str,
    period: int,
    start_ms: int,
    onset_ms: int,
    now_ms: int,
    score: float,
    tracking: Sequence[TrackingSample] = (),
) -> tuple[Metrics, list[str]]:
    """Momentum-shift metrics and evidence (the team's passes and turnovers since onset)."""
    opp = engine.other(team)
    before = engine.team_window(team, period, start_ms, onset_ms)
    after = engine.team_window(team, period, onset_ms, now_ms)
    opp_before = engine.team_window(opp, period, start_ms, onset_ms)
    opp_after = engine.team_window(opp, period, onset_ms, now_ms)
    passes = [
        e
        for e in engine.events_in(period, onset_ms, now_ms)
        if is_counted_pass(e) and e.team_id == team
    ]
    expected = (
        sum(1 - pass_difficulty_of(e) / 100 for e in passes) / len(passes) if passes else None
    )
    pressured = sum(e.under_pressure for e in passes) / len(passes) if passes else None
    metrics: Metrics = {
        "control_before": round(before.control),
        "control_after": round(after.control),
        "possession_before": pct(before.possession_share),
        "possession_after": pct(after.possession_share),
        "pass_accuracy_before": pct(before.pass_accuracy),
        "pass_accuracy_after": pct(after.pass_accuracy),
        "expected_pass_accuracy_after": pct(expected),
        "pressured_passes_after": pct(pressured),
        "passes_after": after.passes,
        "turnovers_after": after.turnovers,
        "own_half_turnovers_after": after.own_half_turnovers,
        "minutes_since_onset": round((now_ms - onset_ms) / MIN, 1),
        "onset_minute": minute(period, onset_ms),
        "minute": minute(period, now_ms),
        "evidence_score": round(score, 1),
    }
    if opp_before.ppda is not None:
        metrics["opponent_ppda_before"] = round(opp_before.ppda, 1)
    if opp_after.ppda is not None:
        metrics["opponent_ppda_after"] = round(opp_after.ppda, 1)
    if tracking:
        metrics["team_hsr_m_per_min_before"] = team_hsr_per_min(
            tracking, team, period, start_ms, onset_ms
        )
        metrics["team_hsr_m_per_min_after"] = team_hsr_per_min(
            tracking, team, period, onset_ms, now_ms
        )
    opp_shots = _shots(engine.events_in(period, onset_ms, now_ms), opp)
    metrics |= {
        "opponent_shots_after": len(opp_shots),
        "opponent_xg_after": _xg(opp_shots),
        **score_at(engine.events, period, now_ms, engine.match.home_team_id),
    }
    evidence = sorted(
        {e.event_id for e in passes}
        | set(after.turnover_event_ids)
        | {e.event_id for e in opp_shots}
    )
    return metrics, evidence


def score_at(events: Sequence[Event], period: int, clock_ms: int, home_team_id: str) -> Metrics:
    """The score after the last event at or before the moment (0-0 before any event).

    A goal's own row carries the score before it, so a goal at the moment is added here.
    """
    upto = _upto(events, period, clock_ms)
    if not upto:
        return {"score_home": 0, "score_away": 0}
    last = upto[-1]
    goal = last.event_type is EventType.SHOT and last.outcome is Outcome.GOAL
    home = goal and last.team_id == home_team_id
    return {
        "score_home": last.score_home + int(home),
        "score_away": last.score_away + int(goal and not home),
    }


def goal(shot: Event, home_team_id: str, events: Sequence[Event] = ()) -> Metrics:
    """The goal, the score after it and, given the events, the match so far: the scorer's
    goals, both teams' shots and xG, and how soon after winning the ball it came."""
    home = shot.team_id == home_team_id
    metrics: Metrics = {
        "xg": round(shot_xg(shot), 2),
        "minute": minute(shot.period, shot.clock_ms),
        "score_home": shot.score_home + int(home),
        "score_away": shot.score_away + int(not home),
        "shot_speed_kmh": round(shot_speed_kmh(shot), 1),
    }
    if not events:
        return metrics
    upto = _upto(events, shot.period, shot.clock_ms)
    team_shots = _shots(upto, shot.team_id)
    opp_shots = [e for e in _shots(upto) if e.team_id not in (None, shot.team_id)]
    metrics |= {
        "player_goals": sum(
            e.outcome is Outcome.GOAL and e.player_id == shot.player_id for e in team_shots
        ),
        "team_shots": len(team_shots),
        "team_xg": _xg(team_shots),
        "opponent_xg": _xg(opp_shots),
    }
    regain = goal_regain(shot, upto)
    if regain is not None:
        metrics["seconds_from_regain"] = round((shot.clock_ms - regain.clock_ms) / 1000)
    return metrics


def goal_regain(shot: Event, events: Sequence[Event]) -> Event | None:
    """The open-play ball win that started the scoring move, if within 15 s of the shot."""
    move = [
        e
        for e in events
        if e.period == shot.period
        and e.possession_id == shot.possession_id
        and (e.period, e.clock_ms) <= (shot.period, shot.clock_ms)
    ]
    first = move[0] if move else None
    if (
        first is None
        or first.team_id != shot.team_id
        or first.event_type not in REGAINS
        or first.play_pattern not in (PlayPattern.REGULAR_PLAY, PlayPattern.FROM_COUNTER)
        or shot.clock_ms - first.clock_ms > REGAIN_TO_SHOT_MS
    ):
        return None
    return first


def players_left(card: Event, home_team_id: str) -> int:
    home = card.team_id == home_team_id
    return (card.players_on_pitch_home if home else card.players_on_pitch_away) - 1


def sending_off(card: Event, home_team_id: str) -> Metrics:
    played = minute(card.period, card.clock_ms)
    return {
        "minute": played,
        "players_left": players_left(card, home_team_id),
        "minutes_left": max(0, round(REGULATION_MIN - played)),
        "score_home": card.score_home,
        "score_away": card.score_away,
    }


def sending_off_impact(
    engine: MatchEngine,
    team: str,
    period: int,
    start_ms: int,
    card_ms: int,
    impact_ms: int,
    left: int,
) -> tuple[Metrics, list[str]]:
    before = engine.team_window(team, period, start_ms, card_ms)
    after = engine.team_window(team, period, card_ms, impact_ms)
    metrics: Metrics = {
        "possession_before": pct(before.possession_share),
        "possession_after": pct(after.possession_share),
        "control_before": round(before.control),
        "control_after": round(after.control),
        "turnovers_after": after.turnovers,
        "players_left": left,
        "window_min": round((impact_ms - card_ms) / MIN),
    }
    opp_shots = _shots(engine.events_in(period, card_ms, impact_ms), engine.other(team))
    metrics |= {"opponent_shots_after": len(opp_shots), "opponent_xg_after": _xg(opp_shots)}
    return metrics, [*after.turnover_event_ids, *(e.event_id for e in opp_shots)]


def penalty(foul: Event) -> Metrics:
    return {
        "minute": minute(foul.period, foul.clock_ms),
        "score_home": foul.score_home,
        "score_away": foul.score_away,
    }


def player_form(shots: Sequence[Event], window_ms: int, events: Sequence[Event] = ()) -> Metrics:
    """Shots in the window and, given the events, the trend against the window before and
    the player's share of the team's shots."""
    metrics: Metrics = {
        "shots": len(shots),
        "npxg": round(sum(shot_xg(s) for s in shots), 2),
        "on_target": sum(s.outcome in (Outcome.GOAL, Outcome.SAVED) for s in shots),
        "goals": sum(s.outcome is Outcome.GOAL for s in shots),
        "window_min": round(window_ms / MIN),
    }
    if not events or not shots:
        return metrics
    last = shots[-1]
    start = last.clock_ms - window_ms
    same_half = [e for e in _upto(events, last.period, last.clock_ms) if e.period == last.period]
    mine = [
        e
        for e in _shots(same_half, last.team_id)
        if e.player_id == last.player_id and not _penalty(e)
    ]
    team = [e for e in _shots(same_half, last.team_id) if e.clock_ms > start and not _penalty(e)]
    metrics["shots_previous"] = sum(start - window_ms < e.clock_ms <= start for e in mine)
    metrics["team_shot_share"] = pct(len(shots) / len(team)) if team else 100.0
    return metrics


def _penalty(shot: Event) -> bool:
    return getattr(shot.details, "shot_type", None) == "penalty"


def pass_milestone(passes: Sequence[Event]) -> Metrics:
    done = sum(x.outcome is Outcome.COMPLETE for x in passes)
    return {
        "completed_passes": done,
        "attempted_passes": len(passes),
        "pass_accuracy": pct(done / len(passes)) if passes else 0.0,
    }


def goal_milestone(goals: Sequence[Event]) -> Metrics:
    return {"goals": len(goals)}


def shot_speed(shot: Event) -> Metrics:
    return {"shot_speed_kmh": round(shot_speed_kmh(shot), 1), "xg": round(shot_xg(shot), 2)}


def pass_quality(pass_event: Event) -> Metrics:
    metrics: Metrics = {
        "difficulty": round(pass_difficulty_of(pass_event)),
        "length_m": round(pass_length_m(pass_event), 1),
        "forward_m": round(pass_forward_m(pass_event), 1),
    }
    speed = pass_ball_speed_kmh(pass_event)
    if speed is not None:
        metrics["ball_speed_kmh"] = round(speed, 1)
    return metrics


def recap(
    engine: MatchEngine, end: Event, home_team_id: str, away_team_id: str
) -> tuple[Metrics, list[str]]:
    """Match totals up to a period end."""
    upto = [x for x in engine.events if (x.period, x.clock_ms) <= (end.period, end.clock_ms)]
    metrics: Metrics = {"score_home": end.score_home, "score_away": end.score_away}
    evidence: list[str] = []
    for side, team in (("home", home_team_id), ("away", away_team_id)):
        shots = [x for x in upto if x.event_type is EventType.SHOT and x.team_id == team]
        passes = [x for x in upto if is_counted_pass(x) and x.team_id == team]
        periods = range(1, end.period + 1)
        live = sum(engine.possession_ms(p, 0, engine.period_end_ms(p) or 0)[team] for p in periods)
        total = sum(
            sum(engine.possession_ms(p, 0, engine.period_end_ms(p) or 0).values()) for p in periods
        )
        done = sum(x.outcome is Outcome.COMPLETE for x in passes)
        metrics |= {
            f"shots_{side}": len(shots),
            f"on_target_{side}": sum(x.outcome in (Outcome.GOAL, Outcome.SAVED) for x in shots),
            f"xg_{side}": round(sum(shot_xg(x) for x in shots), 2),
            f"possession_{side}": pct(live / total if total else 0.5),
            f"passes_{side}": len(passes),
            f"pass_accuracy_{side}": pct(done / len(passes) if passes else None),
        }
        if end.period == 2:
            metrics[f"xg_second_half_{side}"] = _xg([x for x in shots if x.period == 2])
        evidence += [x.event_id for x in shots]
    return metrics, sorted(evidence)
