"""Planted scenario definitions (one YAML file per scenario from Phase 2)."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pitch_ledger.schema.enums import EffectOp, InsightType, ScenarioId
from pitch_ledger.schema.types import PlayerId, TeamId


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScenarioTrigger(_Frozen):
    """When the change starts. ``minute`` is the match minute (60 = period 2 clock 15:00);
    ``duration_min`` ends it early (None: until full time)."""

    period: Annotated[int, Field(ge=1, le=2)]
    minute: Annotated[int, Field(ge=0, le=120)]
    duration_min: Annotated[int, Field(gt=0)] | None = None


class Effect(_Frozen):
    """A change to one generator parameter: ``add`` a value or ``mul`` (multiply) by it."""

    op: EffectOp
    value: float


class ExpectedDetection(_Frozen):
    """What the engine must detect, and how soon after the trigger."""

    insight_type: InsightType
    team_id: TeamId | None = None
    player_id: PlayerId | None = None
    within_min: Annotated[int, Field(gt=0)]


class Scenario(_Frozen):
    """A scripted change injected at a known minute; doubles as a detection test."""

    scenario_id: ScenarioId
    description: str
    trigger: ScenarioTrigger | None = None
    team_id: TeamId | None = None
    player_id: PlayerId | None = None
    effects: dict[str, Effect] = Field(default_factory=dict)
    expected_detection: ExpectedDetection | None = None

    @model_validator(mode="after")
    def _planted_needs_trigger_and_target(self) -> Self:
        if self.scenario_id is ScenarioId.NONE:
            return self
        if self.trigger is None:
            raise ValueError("a planted scenario needs a trigger")
        if (self.team_id is None) == (self.player_id is None):
            raise ValueError("a planted scenario targets exactly one of team_id or player_id")
        return self
