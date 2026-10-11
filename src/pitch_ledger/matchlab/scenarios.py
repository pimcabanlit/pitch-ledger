"""Planted scenarios: YAML definitions, the demo match manifest, and generator plans.

Each ``scenarios/<scenario_id>.yaml`` holds one ``Scenario``; ``scenarios/matches.yaml``
lists the demo matches (pairing, seed, scenario). Effects map onto generator
``Modifiers`` and act only while the scenario is active.

| Effect | Op | Target | Generator change |
| --- | --- | --- | --- |
| pass_success | add | team | completion probability shifted from the difficulty model |
| turnover_rate | mul | team | miscontrol, dispossession and lost-challenge rates |
| top_speed | mul | team | tracking speed ceiling (fatigue) |
| press_rate | mul | team | how often the team presses the ball carrier |
| press_win | mul | team | how often its pressure wins the ball |
| block_depth | add | team | metres the defensive shape moves up the pitch |
| shot_rate | mul | team or player | shot decisions |
| shot_quality | mul | player | preference for high-xG shooting positions |
| involvement | mul | player | how often teammates pass to the player |
| send_off | add | team | straight red cards at the first challenge after the trigger |
"""

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from pitch_ledger.core.config import get_settings
from pitch_ledger.matchlab.styles import NEUTRAL, ModifierProvider, Modifiers, SendOff
from pitch_ledger.schema import Scenario
from pitch_ledger.schema.enums import EffectOp, ScenarioId
from pitch_ledger.schema.types import MatchId, TeamId, UtcTimestamp

TEAM_EFFECTS: dict[str, tuple[EffectOp, str]] = {
    "pass_success": (EffectOp.ADD, "pass_success_add"),
    "turnover_rate": (EffectOp.MUL, "turnover_mult"),
    "top_speed": (EffectOp.MUL, "top_speed_mult"),
    "press_rate": (EffectOp.MUL, "press_mult"),
    "press_win": (EffectOp.MUL, "press_win_mult"),
    "block_depth": (EffectOp.ADD, "block_shift_m"),
    "shot_rate": (EffectOp.MUL, "shot_rate_mult"),
    "send_off": (EffectOp.ADD, ""),
}
PLAYER_EFFECTS: dict[str, tuple[EffectOp, str]] = {
    "shot_rate": (EffectOp.MUL, "focus_shot_mult"),
    "shot_quality": (EffectOp.MUL, "focus_quality_mult"),
    "involvement": (EffectOp.MUL, "focus_involvement_mult"),
}


class ManifestMatch(BaseModel):
    """One demo match: who plays, which scenario is planted, the seed and the kick-off."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    match_id: MatchId
    scenario_id: ScenarioId
    home_team_id: TeamId
    away_team_id: TeamId
    seed: int
    kickoff: UtcTimestamp | None = None  # season order; None: the generator's default


@dataclass(frozen=True)
class ScenarioPlan:
    """What the generator needs to plant a scenario."""

    modifiers: ModifierProvider
    send_offs: tuple[SendOff, ...] = ()
    protected: frozenset[str] = field(default_factory=frozenset)


def _dir(directory: Path | None) -> Path:
    return directory if directory is not None else get_settings().scenarios_dir


def load_scenario(scenario_id: str, directory: Path | None = None) -> Scenario:
    """Load and check ``<directory>/<scenario_id>.yaml``."""
    path = _dir(directory) / f"{scenario_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no scenario file {path}")
    scenario = Scenario.model_validate(yaml.safe_load(path.read_text("utf-8")))
    if scenario.scenario_id != scenario_id:
        raise ValueError(f"{path} defines {scenario.scenario_id}, expected {scenario_id}")
    check_effects(scenario)
    return scenario


def load_manifest(directory: Path | None = None) -> list[ManifestMatch]:
    """Load the demo match list from ``<directory>/matches.yaml``."""
    raw: dict[str, Any] = yaml.safe_load((_dir(directory) / "matches.yaml").read_text("utf-8"))
    return [ManifestMatch.model_validate(m) for m in raw["matches"]]


def check_effects(scenario: Scenario) -> None:
    """Reject unknown effects or the wrong operation for an effect."""
    catalogue = PLAYER_EFFECTS if scenario.player_id is not None else TEAM_EFFECTS
    for name, effect in scenario.effects.items():
        if name not in catalogue:
            raise ValueError(f"{scenario.scenario_id}: effect {name!r} not allowed here")
        op, _ = catalogue[name]
        if effect.op is not op:
            raise ValueError(f"{scenario.scenario_id}: effect {name!r} must use {op.value}")


def trigger_clock(scenario: Scenario) -> tuple[int, int]:
    """(period, clock_ms) at which the scenario starts."""
    assert scenario.trigger is not None
    offset = 45 if scenario.trigger.period == 2 else 0
    return scenario.trigger.period, (scenario.trigger.minute - offset) * 60_000


def plan_for(scenario: Scenario, home_team_id: str, away_team_id: str) -> ScenarioPlan:
    """Turn a scenario into generator inputs for a given pairing."""
    if scenario.scenario_id is ScenarioId.NONE:
        return ScenarioPlan(lambda team, period, clock: NEUTRAL)
    check_effects(scenario)
    team = scenario.team_id or (scenario.player_id or "")[:3]
    if team not in (home_team_id, away_team_id):
        raise ValueError(f"{scenario.scenario_id} targets {team}, not in this match")
    period, start_ms = trigger_clock(scenario)
    assert scenario.trigger is not None
    duration = scenario.trigger.duration_min
    end_ms = start_ms + duration * 60_000 if duration is not None else None

    def active(p: int, clock_ms: int) -> bool:
        if (p, clock_ms) < (period, start_ms):
            return False
        return end_ms is None or (p == period and clock_ms < end_ms)

    catalogue = PLAYER_EFFECTS if scenario.player_id is not None else TEAM_EFFECTS
    changes: dict[str, Any] = {}
    send_offs: list[SendOff] = []
    for name, effect in scenario.effects.items():
        if name == "send_off":
            send_offs += [SendOff(team, period, start_ms)] * int(effect.value)
            continue
        changes[catalogue[name][1]] = effect.value
    on = replace(NEUTRAL, **changes)
    if scenario.player_id is not None:
        on = replace(on, focus_player=scenario.player_id)

    def provider(team_id: str, p: int, clock_ms: int) -> Modifiers:
        return on if team_id == team and active(p, clock_ms) else NEUTRAL

    protected = frozenset({scenario.player_id}) if scenario.player_id else frozenset()
    return ScenarioPlan(provider, tuple(send_offs), protected)
