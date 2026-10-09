"""Planted scenarios: YAML loading, effect checks, active windows and generator hooks."""

from pathlib import Path

import pytest

from pitch_ledger.engine.detector import detect
from pitch_ledger.matchlab.generator import MatchSpec, simulate_match
from pitch_ledger.matchlab.scenarios import load_manifest, load_scenario, plan_for
from pitch_ledger.matchlab.styles import NEUTRAL, SendOff
from pitch_ledger.schema.details import SubstitutionDetails
from pitch_ledger.schema.enums import EventType, Outcome, ScenarioId

M = 60_000


def test_every_scenario_file_loads() -> None:
    for sid in ScenarioId:
        scenario = load_scenario(sid.value)
        assert scenario.scenario_id is sid
        if sid is not ScenarioId.NONE:
            assert scenario.expected_detection is not None
            assert scenario.expected_detection.within_min == 15


def test_manifest_has_the_six_agreed_matches() -> None:
    manifest = load_manifest()
    assert [m.match_id for m in manifest] == [f"M000{i}" for i in range(1, 7)]
    pairs = {m.scenario_id.value: (m.home_team_id, m.away_team_id) for m in manifest[:5]}
    assert pairs == {
        "none": ("T01", "T02"),
        "late_collapse": ("T01", "T02"),
        "high_press_switch": ("T06", "T02"),
        "red_card": ("T03", "T04"),
        "hot_streak": ("T05", "T06"),
    }
    assert (manifest[5].scenario_id, manifest[5].home_team_id) == (ScenarioId.NONE, "T02")
    assert manifest[0].seed == 4211


def _write(tmp_path: Path, body: str) -> Path:
    (tmp_path / "late_collapse.yaml").write_text(
        "scenario_id: late_collapse\ndescription: test\ntrigger: {period: 2, minute: 60}\n"
        "team_id: T02\n" + body,
        "utf-8",
    )
    return tmp_path


@pytest.mark.parametrize(
    "effects",
    [
        "effects:\n  teleport: {op: add, value: 1}\n",  # unknown effect
        "effects:\n  pass_success: {op: mul, value: 0.5}\n",  # wrong operation
        "effects:\n  involvement: {op: mul, value: 2}\n",  # player-only effect on a team
    ],
)
def test_bad_effects_rejected(tmp_path: Path, effects: str) -> None:
    with pytest.raises(ValueError):
        load_scenario("late_collapse", _write(tmp_path, effects))


def test_target_must_play_in_the_match() -> None:
    with pytest.raises(ValueError):
        plan_for(load_scenario("red_card"), "T01", "T02")


def test_effects_only_inside_active_window() -> None:
    plan = plan_for(load_scenario("hot_streak"), "T05", "T06")  # 62' for 15 minutes
    assert plan.modifiers("T05", 2, 17 * M).focus_player == "T05-P09"
    assert plan.modifiers("T05", 2, 17 * M - 1) is NEUTRAL
    assert plan.modifiers("T05", 2, 32 * M) is NEUTRAL
    assert plan.modifiers("T06", 2, 20 * M) is NEUTRAL
    assert plan.protected == frozenset({"T05-P09"})
    collapse = plan_for(load_scenario("late_collapse"), "T01", "T02")
    assert collapse.modifiers("T02", 1, 44 * M) is NEUTRAL
    assert collapse.modifiers("T02", 2, 47 * M).pass_success_add == -0.40


def test_neutral_plan_leaves_the_baseline_untouched() -> None:
    spec = MatchSpec(seed=4211)
    plan = plan_for(load_scenario("none"), "T01", "T02")
    planted = simulate_match(spec, plan.modifiers, plan.send_offs, plan.protected)
    assert planted.events == simulate_match(spec).events


def test_planted_send_off_happens_at_55() -> None:
    plan = plan_for(load_scenario("red_card"), "T03", "T04")
    assert plan.send_offs == (SendOff("T04", 2, 10 * M),)
    sim = simulate_match(
        MatchSpec(home_team_id="T03", away_team_id="T04", seed=4213, scenario_id="red_card"),
        plan.modifiers,
        plan.send_offs,
        plan.protected,
    )
    reds = [e for e in sim.events if e.event_type is EventType.CARD and e.outcome is Outcome.RED]
    assert len(reds) == 1 and reds[0].team_id == "T04"
    assert reds[0].period == 2 and 10 * M <= reds[0].clock_ms <= 13 * M
    after = sim.events[sim.events.index(reds[0]) + 1]
    assert after.players_on_pitch_away == 10


def test_focus_player_is_never_substituted() -> None:
    plan = plan_for(load_scenario("hot_streak"), "T05", "T06")
    for seed in range(1, 6):
        sim = simulate_match(
            MatchSpec(home_team_id="T05", away_team_id="T06", seed=seed,
                      scenario_id="hot_streak"),
            plan.modifiers, plan.send_offs, plan.protected,
        )  # fmt: skip
        outs = [e.details.player_out_id for e in sim.events
                if isinstance(e.details, SubstitutionDetails)]  # fmt: skip
        assert "T05-P09" not in outs


def test_no_impact_trigger_when_half_ends_first() -> None:
    sim = simulate_match(MatchSpec(seed=11), send_offs=(SendOff("T02", 2, 44 * M),))
    rules = [t.rule_id for t in detect(sim.match, sim.events)]
    assert "sending_off" in rules and "sending_off_impact" not in rules
