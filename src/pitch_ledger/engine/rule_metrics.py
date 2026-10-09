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
from pitch_ledger.schema.enums import EventType, Outcome

MIN = 60_000
Metrics = dict[str, int | float]


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
    evidence = sorted({e.event_id for e in passes} | set(after.turnover_event_ids))
    return metrics, evidence


def goal(shot: Event, home_team_id: str) -> Metrics:
    home = shot.team_id == home_team_id
    return {
        "xg": round(shot_xg(shot), 2),
        "minute": minute(shot.period, shot.clock_ms),
        "score_home": shot.score_home + int(home),
        "score_away": shot.score_away + int(not home),
    }


def players_left(card: Event, home_team_id: str) -> int:
    home = card.team_id == home_team_id
    return (card.players_on_pitch_home if home else card.players_on_pitch_away) - 1


def sending_off(card: Event, home_team_id: str) -> Metrics:
    return {
        "minute": minute(card.period, card.clock_ms),
        "players_left": players_left(card, home_team_id),
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
    return metrics, list(after.turnover_event_ids)


def penalty(foul: Event) -> Metrics:
    return {"minute": minute(foul.period, foul.clock_ms)}


def player_form(shots: Sequence[Event], window_ms: int) -> Metrics:
    return {
        "shots": len(shots),
        "npxg": round(sum(shot_xg(s) for s in shots), 2),
        "on_target": sum(s.outcome in (Outcome.GOAL, Outcome.SAVED) for s in shots),
        "goals": sum(s.outcome is Outcome.GOAL for s in shots),
        "window_min": round(window_ms / MIN),
    }


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
    return {"shot_speed_kmh": round(shot_speed_kmh(shot), 1)}


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
            f"xg_{side}": round(sum(shot_xg(x) for x in shots), 2),
            f"possession_{side}": pct(live / total if total else 0.5),
            f"passes_{side}": len(passes),
            f"pass_accuracy_{side}": pct(done / len(passes) if passes else None),
        }
        evidence += [x.event_id for x in shots]
    return metrics, sorted(evidence)
