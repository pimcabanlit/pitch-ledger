"""Structured validation results and the rule catalogue from docs/event-schema.md."""

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class RuleLevel(StrEnum):
    ROW = "row"
    SEQUENCE = "sequence"
    MATCH = "match"
    INSIGHT = "insight"


class RuleAction(StrEnum):
    """``reject`` stops the row and logs it; ``flag`` passes it with a warning; ``block``
    stops an insight from airing."""

    REJECT = "reject"
    FLAG = "flag"
    BLOCK = "block"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    level: RuleLevel
    action: RuleAction
    description: str


_RULES = [
    Rule("ROW-01", RuleLevel.ROW, RuleAction.REJECT,
         "Required fields present; enums valid; outcome valid for event type"),
    Rule("ROW-02", RuleLevel.ROW, RuleAction.REJECT, "Coordinates within 0-105 and 0-68"),
    Rule("ROW-03", RuleLevel.ROW, RuleAction.REJECT,
         "player_id belongs to team_id and is on the pitch (not subbed off or sent off)"),
    Rule("ROW-04", RuleLevel.ROW, RuleAction.FLAG,
         "Shot speed 30-140 km/h; ball speed < 130 km/h; tracking speed < 37 km/h"),
    Rule("SEQ-01", RuleLevel.SEQUENCE, RuleAction.REJECT,
         "seq_no increases by exactly 1; clock_ms never decreases within a period"),
    Rule("SEQ-02", RuleLevel.SEQUENCE, RuleAction.FLAG,
         "A completed pass is followed by a ball_receipt by the intended receiver, same team"),
    Rule("SEQ-03", RuleLevel.SEQUENCE, RuleAction.REJECT,
         "possession_id changes only when possession_team_id changes"),
    Rule("SEQ-04", RuleLevel.SEQUENCE, RuleAction.REJECT,
         "A goal increments the scorer's score on the next event, followed by a kick-off"),
    Rule("SEQ-05", RuleLevel.SEQUENCE, RuleAction.REJECT,
         "related_event_ids point to events that already exist"),
    Rule("MATCH-01", RuleLevel.MATCH, RuleAction.REJECT,
         "Exactly one period_start and one period_end per period"),
    Rule("MATCH-02", RuleLevel.MATCH, RuleAction.FLAG, "Totals within realism bands"),
    Rule("INS-01", RuleLevel.INSIGHT, RuleAction.BLOCK,
         "Every number in an insight's text appears in metrics and recomputes from its evidence"),
]  # fmt: skip

RULES: dict[str, Rule] = {rule.rule_id: rule for rule in _RULES}


class ValidationResult(BaseModel):
    """One rule violation."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    level: RuleLevel
    action: RuleAction
    event_id: str | None
    message: str


def violation(rule_id: str, event_id: str | None, message: str) -> ValidationResult:
    """Build a result for a catalogued rule."""
    rule = RULES[rule_id]
    return ValidationResult(
        rule_id=rule_id, level=rule.level, action=rule.action, event_id=event_id, message=message
    )


@dataclass
class ValidationReport:
    """All results for one match."""

    results: list[ValidationResult]
    events_checked: int = 0
    tracking_checked: int = 0

    @property
    def rejects(self) -> list[ValidationResult]:
        return [r for r in self.results if r.action is RuleAction.REJECT]

    @property
    def flags(self) -> list[ValidationResult]:
        return [r for r in self.results if r.action is RuleAction.FLAG]

    @property
    def ok(self) -> bool:
        """True when nothing was rejected (flags are warnings)."""
        return not self.rejects

    def counts(self) -> Counter[str]:
        return Counter(r.rule_id for r in self.results)

    def for_rule(self, rule_id: str) -> list[ValidationResult]:
        return [r for r in self.results if r.rule_id == rule_id]
