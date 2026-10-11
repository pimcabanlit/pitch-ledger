"""Moment detector: evidence, momentum alerts, rules, and live/batch agreement."""

import pytest

from pitch_ledger.engine import is_counted_pass, match_minute, shot_xg
from pitch_ledger.engine.detector import DetectorConfig, detect
from pitch_ledger.engine.momentum import MomentumConfig, evidence_items, momentum_alerts
from pitch_ledger.matchlab.generator import MatchSpec, SimulatedMatch, simulate_match
from pitch_ledger.matchlab.styles import Modifiers
from pitch_ledger.matchlab.tracking import generate_tracking
from pitch_ledger.schema import ShotDetails, TrackingSample, Trigger
from pitch_ledger.schema.enums import InsightType, Outcome, ShotType

M = 60_000


def collapse(team_id: str, period: int, clock_ms: int) -> Modifiers:
    """T02 falls apart from 60' (period 2 clock 15:00)."""
    if team_id == "T02" and period == 2 and clock_ms >= 15 * M:
        return Modifiers(pass_success_add=-0.4, turnover_mult=3.0)
    return Modifiers()


@pytest.fixture(scope="module")
def baseline() -> SimulatedMatch:
    return simulate_match(MatchSpec(seed=4211))


@pytest.fixture(scope="module")
def collapsed() -> SimulatedMatch:
    return simulate_match(MatchSpec(seed=4212), collapse)


@pytest.fixture(scope="module")
def triggers(baseline: SimulatedMatch) -> list[Trigger]:
    return detect(baseline.match, baseline.events)


# Evidence and momentum --------------------------------------------------------
def test_evidence_signs(baseline: SimulatedMatch) -> None:
    by_id = {e.event_id: e for e in baseline.events}
    items = evidence_items(baseline.events, MomentumConfig())
    for item in items:
        if item.kind == "pass" and item.event_id is not None:
            completed = by_id[item.event_id].outcome is Outcome.COMPLETE
            assert (item.llr < 0) == completed  # a completed pass is evidence against a drop
        if item.kind == "possession":
            assert (item.llr > 0) == (item.event_id is not None)  # a turnover counts for it
    kinds = {i.kind for i in items}
    assert kinds == {"pass", "pressure", "possession"}


def test_baseline_has_no_momentum_alert(baseline: SimulatedMatch) -> None:
    assert momentum_alerts(baseline.events, ("T01", "T02")) == []


def test_planted_collapse_is_detected_after_it_starts(collapsed: SimulatedMatch) -> None:
    alerts = momentum_alerts(collapsed.events, ("T01", "T02"))
    t02 = [a for a in alerts if a.team_id == "T02"]
    assert t02, alerts
    minute = match_minute(t02[0].period, t02[0].clock_ms)
    assert 60 <= minute <= 75
    assert t02[0].onset_ms <= t02[0].clock_ms
    assert t02[0].score >= MomentumConfig().threshold


def test_one_momentum_alert_per_team_per_half(collapsed: SimulatedMatch) -> None:
    alerts = momentum_alerts(collapsed.events, ("T01", "T02"))
    keys = [(a.team_id, a.period) for a in alerts]
    assert len(keys) == len(set(keys))


def test_momentum_trigger_metrics_recompute_from_evidence(collapsed: SimulatedMatch) -> None:
    trigger = next(t for t in detect(collapsed.match, collapsed.events)
                   if t.rule_id == "momentum_shift" and t.subject.team_id == "T02")  # fmt: skip
    by_id = {e.event_id: e for e in collapsed.events}
    passes = [by_id[i] for i in trigger.evidence_event_ids
              if is_counted_pass(by_id[i]) and by_id[i].team_id == "T02"]  # fmt: skip
    assert trigger.metrics["passes_after"] == len(passes)
    done = sum(p.outcome is Outcome.COMPLETE for p in passes)
    assert trigger.metrics["pass_accuracy_after"] == round(100 * done / len(passes), 1)
    assert trigger.metrics["control_after"] < trigger.metrics["control_before"]
    assert trigger.context["after_window"]["end_ms"] == trigger.clock_ms  # type: ignore[index]


# All triggers -------------------------------------------------------------------
def test_triggers_ordered_with_sequential_ids(triggers: list[Trigger]) -> None:
    assert [t.trigger_id for t in triggers] == [
        f"M0001-T{i:04d}" for i in range(1, len(triggers) + 1)
    ]
    keys = [(t.period, t.clock_ms) for t in triggers]
    assert keys == sorted(keys)


def test_every_evidence_id_exists(baseline: SimulatedMatch, triggers: list[Trigger]) -> None:
    ids = {e.event_id for e in baseline.events}
    for t in triggers:
        assert set(t.evidence_event_ids) <= ids, t.rule_id
        assert all(isinstance(v, int | float) for v in t.metrics.values())


def test_recaps_at_each_period_end(triggers: list[Trigger]) -> None:
    recaps = [t for t in triggers if t.insight_type == "recap"]
    assert [t.rule_id for t in recaps] == ["half_time_recap", "full_time_recap"]
    full = recaps[-1].metrics
    assert full["possession_home"] + full["possession_away"] == pytest.approx(100.0, abs=0.2)


@pytest.mark.parametrize("cut", [(1, 30 * M), (2, 10 * M), (2, 40 * M)])
def test_live_and_batch_agree(collapsed: SimulatedMatch, cut: tuple[int, int]) -> None:
    """Triggers from a stream cut at ``cut`` equal the full run's triggers up to ``cut``."""
    prefix = [e for e in collapsed.events if (e.period, e.clock_ms) <= cut]
    live = detect(collapsed.match, prefix)
    full = [t for t in detect(collapsed.match, collapsed.events) if (t.period, t.clock_ms) <= cut]
    assert live == full


def test_goal_trigger_scores_and_evidence() -> None:
    sim = simulate_match(MatchSpec(seed=4212))
    by_id = {e.event_id: e for e in sim.events}
    goals = [t for t in detect(sim.match, sim.events) if t.rule_id == "goal"]
    last = sim.events[-1]
    assert goals and goals[-1].metrics["score_home"] + goals[-1].metrics["score_away"] == (
        last.score_home + last.score_away
    )
    for g in goals:
        shot = by_id[g.evidence_event_ids[0]]
        assert shot.outcome is Outcome.GOAL
        assert g.metrics["xg"] == round(shot_xg(shot), 2)


def test_player_form_counts_non_penalty_shots(collapsed: SimulatedMatch) -> None:
    config = DetectorConfig(form_min_shots=2, form_min_xg=0.1)
    by_id = {e.event_id: e for e in collapsed.events}
    forms = [t for t in detect(collapsed.match, collapsed.events, config=config)
             if t.rule_id == "player_form"]  # fmt: skip
    assert forms
    for t in forms:
        shots = [by_id[i] for i in t.evidence_event_ids]
        assert t.metrics["shots"] == len(shots)
        assert all(isinstance(s.details, ShotDetails) for s in shots)
        assert all(s.details.shot_type is not ShotType.PENALTY for s in shots)  # type: ignore[union-attr]
        assert t.metrics["npxg"] == round(sum(shot_xg(s) for s in shots), 2)


def test_pass_milestone_evidence(triggers: list[Trigger], baseline: SimulatedMatch) -> None:
    by_id = {e.event_id: e for e in baseline.events}
    milestones = [t for t in triggers if t.rule_id == "pass_milestone"]
    assert milestones
    for t in milestones:
        passes = [by_id[i] for i in t.evidence_event_ids]
        assert t.metrics["attempted_passes"] == len(passes)
        assert t.metrics["completed_passes"] == sum(p.outcome is Outcome.COMPLETE for p in passes)
        assert t.metrics["completed_passes"] in (50, 75, 100)


def test_shot_speed_alerts_auto_approve(triggers: list[Trigger]) -> None:
    alerts = [t for t in triggers if t.rule_id == "shot_speed"]
    assert alerts and all(t.auto_approve and t.metrics["shot_speed_kmh"] > 100 for t in alerts)


# Sending off ---------------------------------------------------------------------
def test_sending_off_and_impact(baseline: SimulatedMatch, triggers: list[Trigger]) -> None:
    """M0001 has a natural second yellow at 63.9'."""
    found = {t.rule_id: t for t in triggers}
    card = found["sending_off"]
    impact = found["sending_off_impact"]
    assert card.subject.team_id == "T02" and card.metrics["players_left"] == 10
    assert impact.period == card.period
    assert impact.clock_ms == card.clock_ms + DetectorConfig().sending_off_impact_ms


def test_impact_waits_for_the_stream(baseline: SimulatedMatch, triggers: list[Trigger]) -> None:
    card = next(t for t in triggers if t.rule_id == "sending_off")
    early = [e for e in baseline.events if (e.period, e.clock_ms) <= (2, card.clock_ms + 5 * M)]
    assert not [t for t in detect(baseline.match, early) if t.rule_id == "sending_off_impact"]


# Tracking rules --------------------------------------------------------------------
def test_tracking_rules(baseline: SimulatedMatch) -> None:
    tracking: list[TrackingSample] = generate_tracking(baseline)
    triggers = detect(baseline.match, baseline.events, tracking)
    km = [t for t in triggers if t.rule_id == "distance_milestone"]
    assert km and all(t.metrics["distance_km"] >= 5.0 for t in km)
    per_player = [t.subject.player_id for t in km]
    assert all(per_player.count(p) <= 2 for p in per_player)
    fast = [t for t in triggers if t.rule_id == "top_speed"]
    assert all(t.metrics["top_speed_kmh"] >= 34.0 and t.auto_approve for t in fast)
    assert len({t.subject.player_id for t in fast}) == len(fast)
    momentum = [t for t in triggers if t.rule_id == "momentum_shift"]
    assert momentum == []


# Context metrics and prioritisation ----------------------------------------------------
@pytest.fixture(scope="module")
def demo_m0002() -> tuple[SimulatedMatch, list[TrackingSample], list[Trigger]]:
    from pitch_ledger.matchlab.build import build_match

    sim, tracking = build_match(MatchSpec(match_id="M0002", seed=4212,
                                          scenario_id="late_collapse"))  # fmt: skip
    return sim, tracking, detect(sim.match, sim.events, tracking)


def test_every_trigger_recomputes_with_its_context(
    demo_m0002: tuple[SimulatedMatch, list[TrackingSample], list[Trigger]],
) -> None:
    from pitch_ledger.engine.recompute import recompute_trigger

    sim, tracking, triggers = demo_m0002
    by_id = {e.event_id: e for e in sim.events}
    for t in triggers:
        assert recompute_trigger(t, sim.match, sim.events, tracking) == t.metrics, t.trigger_id
        for i in t.evidence_event_ids:  # evidence exists and is not after the moment
            assert (by_id[i].period, by_id[i].clock_ms) <= (t.period, t.clock_ms)
    goal = next(t for t in triggers if t.rule_id == "goal")
    assert {"player_goals", "team_shots", "team_xg", "opponent_xg", "shot_speed_kmh"} <= set(
        goal.metrics
    )
    momentum = next(t for t in triggers if t.rule_id == "momentum_shift")
    assert {"opponent_shots_after", "opponent_xg_after", "score_home"} <= set(momentum.metrics)


def test_goal_from_a_regain_cites_the_ball_win(
    demo_m0002: tuple[SimulatedMatch, list[TrackingSample], list[Trigger]],
) -> None:
    sim, _, triggers = demo_m0002
    by_id = {e.event_id: e for e in sim.events}
    quick = [t for t in triggers if t.rule_id == "goal" and "seconds_from_regain" in t.metrics]
    assert quick, "the demo's hat-trick goal comes 5 s after a ball win"
    for t in quick:
        shot, regain = by_id[t.evidence_event_ids[0]], by_id[t.evidence_event_ids[-1]]
        assert regain.team_id == shot.team_id and regain.possession_id == shot.possession_id
        assert 0 <= (shot.clock_ms - regain.clock_ms) / 1000 <= 15
        assert t.metrics["seconds_from_regain"] == round((shot.clock_ms - regain.clock_ms) / 1000)
    scorers = [t.metrics["player_goals"] for t in triggers if t.rule_id == "goal"]
    assert max(scorers) == 3  # the hat-trick


def test_speed_alert_on_a_goal_is_folded_into_the_goal(
    demo_m0002: tuple[SimulatedMatch, list[TrackingSample], list[Trigger]],
) -> None:
    _, _, triggers = demo_m0002
    goals = {t.evidence_event_ids[0]: t for t in triggers if t.rule_id == "goal"}
    speeds = [t for t in triggers if t.rule_id == "shot_speed"]
    on_goals = [t for t in speeds if t.evidence_event_ids[0] in goals]
    assert on_goals
    for t in on_goals:
        assert t.context["superseded_by"] == "goal"
        goal = goals[t.evidence_event_ids[0]]
        assert goal.metrics["shot_speed_kmh"] == t.metrics["shot_speed_kmh"]


def test_slower_repeat_speed_alerts_are_folded(baseline: SimulatedMatch) -> None:
    config = DetectorConfig(shot_speed_alert_kmh=60.0)  # many alerts
    speeds = [t for t in detect(baseline.match, baseline.events, config=config)
              if t.rule_id == "shot_speed"]  # fmt: skip
    aired: dict[str, Trigger] = {}
    for t in speeds:
        player = t.subject.player_id or ""
        prev = aired.get(player)
        reason = t.context.get("superseded_by")
        if reason == "cooldown":
            assert prev is not None and prev.period == t.period
            assert t.clock_ms - prev.clock_ms < config.shot_speed_cooldown_ms
            assert t.metrics["shot_speed_kmh"] <= prev.metrics["shot_speed_kmh"]
        elif reason in (None, "cap"):  # the cooldown compares with the player's last alert
            aired[player] = t  # not folded by a goal or the cooldown (capped ones count)
    assert any(t.context.get("superseded_by") == "cooldown" for t in speeds)


def test_key_moments_are_never_folded(
    demo_m0002: tuple[SimulatedMatch, list[TrackingSample], list[Trigger]],
) -> None:
    _, _, triggers = demo_m0002
    assert all("superseded_by" not in t.context for t in triggers
               if t.insight_type is not InsightType.SPEED_ALERT)  # fmt: skip


def _speed(t: Trigger) -> float:
    return float(t.metrics.get("shot_speed_kmh", t.metrics.get("top_speed_kmh", 0.0)))


def test_speed_badges_are_capped_per_team_half_unless_a_record(baseline: SimulatedMatch) -> None:
    """One badge per team per half airs; a later one only if it is the fastest of its kind
    so far (the first of each kind included). Nothing is dropped from the trigger list."""
    config = DetectorConfig(shot_speed_alert_kmh=60.0)  # many alerts
    uncapped = DetectorConfig(shot_speed_alert_kmh=60.0, speed_badges_per_team_half=99)
    triggers = detect(baseline.match, baseline.events, config=config)
    assert len(triggers) == len(detect(baseline.match, baseline.events, config=uncapped))
    best: dict[str, float] = {}
    aired: dict[tuple[str | None, int], int] = {}
    for t in triggers:
        if t.insight_type is not InsightType.SPEED_ALERT:
            continue
        reason, record = t.context.get("superseded_by"), _speed(t) > best.get(t.rule_id, 0.0)
        key = (t.subject.team_id, t.period)
        if reason is None:
            assert aired.get(key, 0) < 1 or record
            aired[key] = aired.get(key, 0) + 1
        elif reason == "cap":
            assert aired.get(key, 0) >= 1 and not record
        best[t.rule_id] = max(best.get(t.rule_id, 0.0), _speed(t))
    assert any(t.context.get("superseded_by") == "cap" for t in triggers)
