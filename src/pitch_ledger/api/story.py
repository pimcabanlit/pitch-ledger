"""The Insight Story: one aired insight traced end to end, built from what the ledger holds.

Detected (the detector's trigger) → Analyzed (engine metrics over the evidence events) →
Explained (drafts) → Audited (checks, revisions, verdict) → Approved (producer or an
auto-approval rule) → Published (overlays per audience mode and language). Every detail
line is derived from records; nothing here computes a football number. Public responses
carry no producer identity.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pitch_ledger.api.schemas import (
    BroadcastAudit,
    BroadcastGraphic,
    BroadcastPackage,
    EvidenceEvent,
    InsightStory,
    MatchSummary,
    StoryStage,
    StoryStep,
    StoryTrigger,
    StoryWindow,
)
from pitch_ledger.engine.windows import match_minute
from pitch_ledger.eventstore.rows import insight_status
from pitch_ledger.ops.alerts import minute_label
from pitch_ledger.schema import AuditRecord, Insight, Overlay, Trigger
from pitch_ledger.validation.insight_rules import unsupported_numbers

PRODUCER_ACTIONS = {"producer_approve", "producer_block"}
DRAFT_ACTIONS = {"draft", "fallback"}
VERDICTS = {"approve", "correct", "block"}
AUTO_RULE = "auto-approval rule"


def approval_label(approved_by: str | None) -> str | None:
    """Who approved, without identity: "producer" or the auto-approval rule."""
    if approved_by is None:
        return None
    return approved_by if approved_by.startswith(AUTO_RULE) else "producer"


def evidence_events(
    events: Mapping[str, Mapping[str, Any]], ids: Sequence[str]
) -> list[EvidenceEvent]:
    """The cited events that exist in the match files, in citation order."""
    return [
        EvidenceEvent(
            event_id=e["event_id"],
            minute=round(match_minute(e["period"], int(e["clock_ms"])), 2),
            event_type=e["event_type"],
            team_id=e["team_id"],
            player_id=e["player_id"],
            outcome=e["outcome"],
        )
        for e in (events[i] for i in ids if i in events)
    ]


def _step(record: AuditRecord, allowed: Sequence[float]) -> StoryStep:
    producer = record.action in PRODUCER_ACTIONS
    text = " ".join(x for x in (record.headline, record.explanation) if x)
    return StoryStep(
        audit_id=record.audit_id,
        round=record.round,
        agent="producer" if producer else record.actor,
        role="producer" if producer else "test" if record.actor == "test" else "agent",
        action=record.action,
        passed=record.passed,
        findings=[] if producer else list(record.findings),  # a block reason may name a person
        headline=record.headline,
        explanation=record.explanation,
        unsupported_numbers=unsupported_numbers(text, allowed) if text else [],
        recorded_at=record.recorded_at,
    )


def _windows(trigger: Trigger) -> list[StoryWindow]:
    found = []
    for key, label in (("before_window", "before"), ("after_window", "after")):
        w = trigger.context.get(key)
        if isinstance(w, dict) and {"period", "start_ms", "end_ms"} <= w.keys():
            period, start, end = (
                int(str(w["period"])),
                int(str(w["start_ms"])),
                int(str(w["end_ms"])),
            )
            found.append(StoryWindow(label=label,
                                     start_minute=round(match_minute(period, start), 1),
                                     end_minute=round(match_minute(period, end), 1)))  # fmt: skip
    return found


def _first(steps: Sequence[StoryStep], actions: set[str]) -> datetime | None:
    return next((s.recorded_at for s in steps if s.action in actions), None)


def _stages(
    insight: Insight,
    trigger: Trigger | None,
    steps: Sequence[StoryStep],
    evidence: Sequence[EvidenceEvent],
    overlays: Sequence[Overlay],
    status: str,
) -> list[StoryStage]:
    minute = minute_label(insight.period, insight.clock_ms)
    rule = (trigger.rule_id if trigger else insight.insight_type.value).replace("_", " ")
    windows = _windows(trigger) if trigger else []
    after = next((w for w in windows if w.label == "after"), None)
    span = f" ({after.start_minute:.0f}'–{after.end_minute:.0f}')" if after else ""  # noqa: RUF001
    computed = len(trigger.metrics) if trigger else len(insight.metrics)
    drafts = [s for s in steps if s.action in DRAFT_ACTIONS]
    revisions = sum(s.action == "revise" for s in steps)
    agent = insight.created_by_agent
    if drafts:
        explained = f"{len(drafts)} draft{'s' * (len(drafts) > 1)} by the {agent}"
        if any(s.action == "fallback" for s in drafts):
            explained += " (template fallback used)"
    else:
        explained = "Template copy from computed numbers (low-risk type, no model)"
    verdict = insight.audit_status.value
    audited = {
        "approved": "Passed first time: every number recomputed from the evidence",
        "corrected": f"Corrected after {revisions} revision{'s' * (revisions != 1)}: "
        "the Auditor caught an unsupported number",
        "blocked": "Blocked by the Auditor: never airs",
    }.get(verdict, "Waiting for the Auditor")
    approval = approval_label(insight.approved_by)
    approved_at = _first(steps, {"producer_approve"}) or _first(steps, VERDICTS)
    modes = sorted({o.audience_mode.value for o in overlays})
    languages = sorted({o.language for o in overlays})
    published = (
        f"{len(overlays)} overlays: {len(modes)} audience modes × {len(languages)} languages"  # noqa: RUF001
        if overlays
        else "Not on air"
    )
    return [
        StoryStage(key="detected", label="Detected", done=True,
                   detail=f"{rule.capitalize()} at {minute}"),
        StoryStage(key="analyzed", label="Analyzed", done=True,
                   detail=f"{computed} metrics computed in code from {len(evidence)} evidence "
                   f"events{span}; {len(insight.metrics)} cited"),
        StoryStage(key="explained", label="Explained", done=True, detail=explained,
                   at=_first(steps, DRAFT_ACTIONS)),
        StoryStage(key="audited", label="Audited", done=verdict != "pending", detail=audited,
                   at=_first(steps, VERDICTS)),
        StoryStage(key="approved", label="Approved", done=approval is not None,
                   detail=f"Approved by the {approval}" if approval == "producer"
                   else f"Auto-approved ({approval})" if approval else "Waiting for a producer",
                   at=approved_at if approval else None),
        StoryStage(key="published", label="Published", done=status == "aired" and bool(overlays),
                   detail=published),
    ]  # fmt: skip


def build_story(
    match: MatchSummary,
    insight: Insight,
    trigger: Trigger | None,
    audits: Sequence[AuditRecord],
    overlays: Sequence[Overlay],
    events: Mapping[str, Mapping[str, Any]],
) -> InsightStory:
    allowed = list((trigger.metrics if trigger else insight.metrics).values())
    steps = [_step(a, allowed) for a in audits]
    evidence = evidence_events(events, insight.evidence_event_ids)
    status = insight_status(insight)
    ordered = sorted(overlays, key=lambda o: o.overlay_id)
    story_trigger = None
    if trigger is not None:
        story_trigger = StoryTrigger(
            trigger_id=trigger.trigger_id,
            rule_id=trigger.rule_id,
            minute=round(match_minute(trigger.period, trigger.clock_ms), 1),
            detector=str(detector) if (detector := trigger.context.get("detector")) else None,
            metrics=trigger.metrics,
            windows=_windows(trigger),
        )
    return InsightStory(
        match=match,
        insight=insight.model_copy(update={"approved_by": approval_label(insight.approved_by)}),
        status=status,
        minute=round(match_minute(insight.period, insight.clock_ms), 1),
        trigger=story_trigger,
        stages=_stages(insight, trigger, steps, evidence, ordered, status),
        steps=steps,
        evidence=evidence,
        evidence_counts=dict(Counter(e.event_type for e in evidence).most_common()),
        overlays=ordered,
    )


def build_broadcast(
    insight: Insight, audits: Sequence[AuditRecord], overlays: Sequence[Overlay]
) -> BroadcastPackage:
    graphics = [
        BroadcastGraphic(mode=o.audience_mode.value, language=o.language,
                         template=o.template.value, display_ms=o.display_ms,
                         priority=o.priority, text=o.text, data=dict(o.data))
        for o in sorted(overlays, key=lambda o: o.overlay_id)
    ]  # fmt: skip
    return BroadcastPackage(
        insight_id=insight.insight_id,
        match_id=insight.match_id,
        period=insight.period,
        clock_ms=insight.clock_ms,
        match_minute=round(match_minute(insight.period, insight.clock_ms), 2),
        insight_type=insight.insight_type.value,
        team_id=insight.subject.team_id,
        player_id=insight.subject.player_id,
        headline=insight.headline,
        metrics=insight.metrics,
        evidence_event_ids=insight.evidence_event_ids,
        graphics=graphics,
        audit=BroadcastAudit(
            status=insight.audit_status.value,
            drafts=sum(a.action in DRAFT_ACTIONS for a in audits),
            revisions=sum(a.action == "revise" for a in audits),
            approval=approval_label(insight.approved_by),
            model_version=insight.model_version,
        ),
    )
