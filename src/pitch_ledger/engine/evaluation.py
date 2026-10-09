"""Judge detector output against a planted scenario's expected detection."""

from collections.abc import Sequence
from dataclasses import dataclass

from pitch_ledger.engine.windows import match_minute
from pitch_ledger.schema import Scenario, Trigger
from pitch_ledger.schema.enums import InsightType


@dataclass(frozen=True)
class DetectionCheck:
    """Did the detector find the planted change in time, without false momentum alerts?

    False momentum alerts are momentum shifts before the planted minute, or any momentum
    shift in a baseline (``none``) match.
    """

    scenario: Scenario
    hit: Trigger | None
    latency_min: float | None
    false_momentum: tuple[Trigger, ...]

    @property
    def passed(self) -> bool:
        found = self.scenario.expected_detection is None or self.hit is not None
        return found and not self.false_momentum


def _matches(trigger: Trigger, scenario: Scenario) -> bool:
    expected = scenario.expected_detection
    assert expected is not None
    if trigger.insight_type is not expected.insight_type:
        return False
    if expected.team_id is not None and trigger.subject.team_id != expected.team_id:
        return False
    return expected.player_id is None or trigger.subject.player_id == expected.player_id


def check_detection(scenario: Scenario, triggers: Sequence[Trigger]) -> DetectionCheck:
    """Compare triggers with ``scenario.expected_detection``."""
    start = scenario.trigger.minute if scenario.trigger is not None else None
    momentum = [t for t in triggers if t.insight_type is InsightType.MOMENTUM_SHIFT]
    if start is None:
        return DetectionCheck(scenario, None, None, tuple(momentum))
    false = tuple(t for t in momentum if match_minute(t.period, t.clock_ms) < start)
    expected = scenario.expected_detection
    if expected is None:
        return DetectionCheck(scenario, None, None, false)
    for trigger in triggers:
        latency = match_minute(trigger.period, trigger.clock_ms) - start
        if 0 <= latency <= expected.within_min and _matches(trigger, scenario):
            return DetectionCheck(scenario, trigger, round(latency, 1), false)
    return DetectionCheck(scenario, None, None, false)
