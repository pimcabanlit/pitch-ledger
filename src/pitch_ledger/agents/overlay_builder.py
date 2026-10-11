"""Overlay builder (code, not a model): template, timing, priority and data for each copy."""

from typing import Any

from pitch_ledger.schema import Insight, Overlay, Trigger
from pitch_ledger.schema.enums import AudienceMode, OverlayTemplate

TEMPLATE_BY_RULE: dict[str, OverlayTemplate] = {
    "momentum_shift": OverlayTemplate.MOMENTUM_BAR,
    "goal": OverlayTemplate.STAT_CARD,
    "sending_off": OverlayTemplate.LOWER_THIRD,
    "sending_off_impact": OverlayTemplate.STAT_CARD,
    "penalty_awarded": OverlayTemplate.LOWER_THIRD,
    "player_form": OverlayTemplate.PLAYER_TAG,
    "half_time_recap": OverlayTemplate.RECAP_CARD,
    "full_time_recap": OverlayTemplate.RECAP_CARD,
    "shot_speed": OverlayTemplate.SPEED_BADGE,
    "top_speed": OverlayTemplate.SPEED_BADGE,
    "pass_milestone": OverlayTemplate.MILESTONE_BADGE,
    "distance_milestone": OverlayTemplate.MILESTONE_BADGE,
    "pass_quality": OverlayTemplate.PASS_BADGE,
}
DISPLAY_MS: dict[OverlayTemplate, int] = {
    OverlayTemplate.MOMENTUM_BAR: 10_000,
    OverlayTemplate.STAT_CARD: 8_000,
    OverlayTemplate.LOWER_THIRD: 6_000,
    OverlayTemplate.PLAYER_TAG: 6_000,
    OverlayTemplate.SPEED_BADGE: 4_000,
    OverlayTemplate.RECAP_CARD: 15_000,
    OverlayTemplate.MILESTONE_BADGE: 4_000,
    OverlayTemplate.PASS_BADGE: 4_000,
}
PRIORITY: dict[str, int] = {
    "key_moment": 1,
    "momentum_shift": 2,
    "recap": 2,
    "player_form": 3,
    "speed_alert": 4,
    "milestone": 4,
    "pass_quality": 4,
}


def overlay_data(trigger: Trigger, insight: Insight) -> dict[str, Any]:
    """Structured data the Studio renders (numbers only from the audited trigger)."""
    data: dict[str, Any] = {
        "team_id": trigger.subject.team_id,
        "player_id": trigger.subject.player_id,
        "metrics": dict(insight.metrics),
    }
    if "control_before" in trigger.metrics and "control_after" in trigger.metrics:
        data["control"] = [trigger.metrics["control_before"], trigger.metrics["control_after"]]
    return data


def build_overlay(
    overlay_id: str,
    trigger: Trigger,
    insight: Insight,
    mode: str,
    language: str,
    text: str,
) -> Overlay:
    template = TEMPLATE_BY_RULE.get(trigger.rule_id, OverlayTemplate.LOWER_THIRD)
    return Overlay(
        overlay_id=overlay_id,
        insight_id=insight.insight_id,
        period=trigger.period,
        trigger_clock_ms=trigger.clock_ms,
        display_ms=DISPLAY_MS[template],
        priority=PRIORITY.get(trigger.insight_type.value, 5),
        template=template,
        audience_mode=AudienceMode(mode),
        language=language,
        text=text,
        data=overlay_data(trigger, insight),
    )
