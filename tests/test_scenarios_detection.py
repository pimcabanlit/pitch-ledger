"""Phase 2 acceptance: each planted scenario is detected within its limit, and baselines
raise no false momentum alerts, on the demo matches and across a sweep of seeds."""

import pytest

from pitch_ledger.engine.detector import detect
from pitch_ledger.engine.evaluation import check_detection
from pitch_ledger.matchlab.build import build_match
from pitch_ledger.matchlab.generator import MatchSpec, simulate_match
from pitch_ledger.matchlab.scenarios import ManifestMatch, load_manifest, load_scenario, plan_for
from pitch_ledger.schema.enums import InsightType, ScenarioId

MANIFEST = load_manifest()
PLANTED = [m for m in MANIFEST if m.scenario_id is not ScenarioId.NONE]
BASELINES = [m for m in MANIFEST if m.scenario_id is ScenarioId.NONE]


def _spec(entry: ManifestMatch, seed: int | None = None) -> MatchSpec:
    return MatchSpec(
        match_id=entry.match_id,
        home_team_id=entry.home_team_id,
        away_team_id=entry.away_team_id,
        seed=entry.seed if seed is None else seed,
        scenario_id=entry.scenario_id.value,
    )


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda m: f"{m.match_id}-{m.scenario_id.value}")
def test_demo_match_detection(entry: ManifestMatch) -> None:
    sim, tracking = build_match(_spec(entry))
    scenario = load_scenario(entry.scenario_id.value)
    triggers = detect(sim.match, sim.events, tracking)
    check = check_detection(scenario, triggers)
    assert check.passed, (check.hit, check.false_momentum)
    if scenario.expected_detection is not None:
        assert check.latency_min is not None and check.latency_min <= 10
    else:
        assert not [t for t in triggers if t.insight_type is InsightType.MOMENTUM_SHIFT]


@pytest.mark.parametrize("entry", PLANTED, ids=lambda m: m.scenario_id.value)
def test_planted_scenario_detected_across_seeds(entry: ManifestMatch) -> None:
    scenario = load_scenario(entry.scenario_id.value)
    plan = plan_for(scenario, entry.home_team_id, entry.away_team_id)
    hits = 0
    for seed in range(1, 11):
        sim = simulate_match(_spec(entry, seed), plan.modifiers, plan.send_offs, plan.protected)
        hits += check_detection(scenario, detect(sim.match, sim.events)).hit is not None
    assert hits >= 8


def test_baselines_rarely_raise_momentum_alerts() -> None:
    alerting = 0
    for entry in BASELINES:
        for seed in range(1, 11):
            sim = simulate_match(_spec(entry, seed))
            triggers = detect(sim.match, sim.events)
            alerting += any(t.insight_type is InsightType.MOMENTUM_SHIFT for t in triggers)
    assert alerting <= 1
