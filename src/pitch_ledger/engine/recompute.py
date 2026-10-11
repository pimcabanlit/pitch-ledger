"""Recompute a trigger's metrics from the raw rows, as the Auditor's check.

Only events (and tracking) up to the trigger's moment are used, with the windows recorded
in the trigger's context and its evidence events. The arithmetic is the detector's own
(``engine.rule_metrics``), so an honest trigger recomputes exactly; a tampered metric, or
a number an agent made up, does not.
"""

from collections.abc import Sequence
from typing import Any

from pitch_ledger.engine import rule_metrics as rm
from pitch_ledger.engine.momentum import MomentumConfig, momentum_alerts
from pitch_ledger.engine.physical import physical
from pitch_ledger.engine.season import SeasonView, goal_metrics, recap_metrics
from pitch_ledger.engine.windows import MatchEngine
from pitch_ledger.schema import Event, Match, TrackingSample, Trigger
from pitch_ledger.schema.enums import EventType

Metrics = dict[str, int | float]


class NotRecomputable(ValueError):
    """The trigger's rule or source data does not allow a recompute."""


def _window(trigger: Trigger, key: str) -> tuple[int, int, int]:
    raw: Any = trigger.context.get(key)
    if not isinstance(raw, dict):
        raise NotRecomputable(f"{trigger.trigger_id}: context has no {key}")
    return int(raw["period"]), int(raw["start_ms"]), int(raw["end_ms"])


def _season(trigger: Trigger, season: SeasonView | None) -> SeasonView | None:
    """The season view to recompute season facts with, if the trigger used any: it must
    hold exactly the earlier matches the trigger lists."""
    claimed: Any = trigger.context.get("season")
    if not claimed:
        return None
    if season is None or season.match_ids != list(claimed.get("matches", [])):
        raise NotRecomputable(f"{trigger.trigger_id}: season records differ from the trigger's")
    return season


def recompute_trigger(
    trigger: Trigger,
    match: Match,
    events: Sequence[Event],
    tracking: Sequence[TrackingSample] = (),
    momentum_config: MomentumConfig | None = None,
    season: SeasonView | None = None,
) -> Metrics:
    """Every metric of ``trigger``, recomputed from source rows up to its moment (and, for
    season facts, from the earlier matches' records in ``season``)."""
    moment = (trigger.period, trigger.clock_ms)
    upto = [e for e in events if (e.period, e.clock_ms) <= moment]
    samples = [s for s in tracking if (s.period, s.clock_ms) <= moment]
    by_id = {e.event_id: e for e in upto}
    evidence = [by_id[i] for i in trigger.evidence_event_ids if i in by_id]
    if len(evidence) != len(trigger.evidence_event_ids):
        raise NotRecomputable(f"{trigger.trigger_id}: evidence missing or after the moment")
    rule = trigger.rule_id
    home = match.home_team_id

    if rule == "momentum_shift":
        team = trigger.subject.team_id
        assert team is not None
        period, start, onset = _window(trigger, "before_window")
        _, _, now = _window(trigger, "after_window")
        engine = MatchEngine(match, upto)
        alerts = momentum_alerts(upto, engine.teams, momentum_config)
        score = next(
            (a.score for a in alerts if (a.team_id, a.period, a.clock_ms) == (team, period, now)),
            None,
        )
        if score is None:
            raise NotRecomputable(f"{trigger.trigger_id}: no momentum alert at that moment")
        metrics, _ = rm.momentum(engine, team, period, start, onset, now, score, samples)
        return metrics
    if rule == "goal":
        metrics = rm.goal(evidence[0], home, upto)
        if (view := _season(trigger, season)) is not None:
            metrics |= goal_metrics(view, evidence[0], int(metrics.get("player_goals", 0)))
        return metrics
    if rule == "sending_off":
        return rm.sending_off(evidence[0], home)
    if rule == "sending_off_impact":
        team = trigger.subject.team_id
        assert team is not None
        card = by_id[str(trigger.context.get("card_event_id", trigger.evidence_event_ids[0]))]
        period, start, card_ms = _window(trigger, "before_window")
        _, _, impact_ms = _window(trigger, "after_window")
        metrics, _ = rm.sending_off_impact(
            MatchEngine(match, upto),
            team,
            period,
            start,
            card_ms,
            impact_ms,
            rm.players_left(card, home),
        )
        return metrics
    if rule == "penalty_awarded":
        return rm.penalty(evidence[0])
    if rule == "player_form":
        period, start, end = _window(trigger, "window")
        return rm.player_form(evidence, end - start, upto)
    if rule == "pass_milestone":
        return rm.pass_milestone(evidence)
    if rule in ("brace", "hat_trick"):
        return rm.goal_milestone(evidence)
    if rule == "shot_speed":
        return rm.shot_speed(evidence[0])
    if rule == "pass_quality":
        return rm.pass_quality(evidence[0])
    if rule in ("half_time_recap", "full_time_recap"):
        period_end = next(
            e
            for e in reversed(upto)
            if e.event_type is EventType.PERIOD_END and e.period == trigger.period
        )
        metrics, _ = rm.recap(MatchEngine(match, upto), period_end, home, match.away_team_id)
        if (view := _season(trigger, season)) is not None and rule == "full_time_recap":
            metrics |= recap_metrics(view, match, period_end.score_home, period_end.score_away)
        return metrics
    if rule == "top_speed":
        player = trigger.subject.player_id
        sample = next(
            (s for s in samples if s.player_id == player and (s.period, s.clock_ms) == moment),
            None,
        )
        if sample is None:
            raise NotRecomputable(f"{trigger.trigger_id}: no tracking sample at that moment")
        return {"top_speed_kmh": round(sample.speed_kmh, 1)}
    if rule == "distance_milestone":
        player = trigger.subject.player_id
        stats = physical([s for s in samples if s.player_id == player]).get(player or "")
        if stats is None:
            raise NotRecomputable(f"{trigger.trigger_id}: no tracking for {player}")
        return {"distance_km": round(stats.distance_m / 1000, 1)}
    raise NotRecomputable(f"{trigger.trigger_id}: rule {rule} cannot be recomputed")


def recompute_metric(
    trigger: Trigger,
    name: str,
    match: Match,
    events: Sequence[Event],
    tracking: Sequence[TrackingSample] = (),
    season: SeasonView | None = None,
) -> int | float:
    """One metric of ``trigger``, recomputed from source rows."""
    metrics = recompute_trigger(trigger, match, events, tracking, season=season)
    if name not in metrics:
        raise NotRecomputable(f"{trigger.trigger_id}: no metric {name}")
    return metrics[name]
