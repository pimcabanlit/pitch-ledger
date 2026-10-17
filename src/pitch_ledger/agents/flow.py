"""The stages one trigger goes through, each writing to the ledger and its audit trail.

1. ``explain_and_audit``: Explainer (or Narrator) drafts; the Auditor checks; findings go
   back at most twice; the insight ends ``approved``, ``corrected`` or ``blocked``.
2. ``decide``: the producer (or the auto-approval rule for low-risk speed alerts)
   approves or holds an audited insight.
3. ``personalize_modes`` then ``localize_mode`` (one per audience mode, concurrently): the
   Personalizer writes every mode in English in one call, and the Translator renders each
   mode in the other languages in one call; every copy passes the number and editorial
   checks or falls back to template copy.
4. ``publish``: the Overlay builder turns each copy into an overlay in the ledger.
5. ``tell_story`` (full route only): the Narrator and Translator write the "Tell me more"
   beats from code-computed facts; each beat passes the story checks or falls back to its
   template.
"""

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import product

from pitch_ledger.agents import copy
from pitch_ledger.agents.auditor import MAX_REVISIONS, Auditor, AuditResult
from pitch_ledger.agents.contracts import ExplainRequest, Facts, InsightDraft
from pitch_ledger.agents.copy_check import CheckedCopy
from pitch_ledger.agents.editorial import is_style
from pitch_ledger.agents.explainer import draft_insight, plant_error, template_draft
from pitch_ledger.agents.model_manager import ModelManager, ModelUnavailable
from pitch_ledger.agents.overlay_builder import build_overlay
from pitch_ledger.agents.personalizer import personalize
from pitch_ledger.agents.story import StoryFacts
from pitch_ledger.agents.storyteller import tell
from pitch_ledger.agents.translator import translate
from pitch_ledger.ledger.repository import Ledger
from pitch_ledger.schema import AuditRecord, Insight, Overlay, StoryRecord, Trigger
from pitch_ledger.schema.audit import AuditAction
from pitch_ledger.schema.enums import AuditStatus, InsightType

FULL_FLOW = frozenset(
    {InsightType.MOMENTUM_SHIFT, InsightType.KEY_MOMENT, InsightType.PLAYER_FORM, InsightType.RECAP}
)
"""Insight types that go through Explainer, Auditor, producer and personalization."""
AUTO_APPROVE = frozenset({InsightType.SPEED_ALERT, InsightType.MILESTONE, InsightType.PASS_QUALITY})
"""Low-risk types (badges): template copy, code audit, auto-approved, no model calls."""
LANGUAGES = ("en", "es", "fil")


@dataclass
class Services:
    match_id: str
    models: ModelManager
    auditor: Auditor
    ledger: Ledger
    inject_error_for: set[str] = field(default_factory=set)
    languages: tuple[str, ...] = LANGUAGES
    modes: tuple[str, ...] = copy.MODES

    @property
    def model_version(self) -> str:
        return self.models.version

    def audit(
        self,
        insight_id: str,
        trigger: Trigger,
        round_no: int,
        actor: str,
        action: AuditAction,
        passed: bool | None = None,
        findings: Iterable[str] = (),
        draft: InsightDraft | None = None,
    ) -> AuditRecord:
        record = AuditRecord(
            audit_id=self.ledger.next_id(self.match_id, "A"),
            match_id=self.match_id,
            insight_id=insight_id,
            trigger_id=trigger.trigger_id,
            round=round_no,
            actor=actor,
            action=action,
            passed=passed,
            findings=list(findings),
            headline=draft.headline if draft else None,
            explanation=draft.explanation if draft else None,
            recorded_at=datetime.now(UTC),
        )
        self.ledger.add_audit(record)
        return record


def confidence(trigger: Trigger) -> float:
    """How sure the engine is (code-assigned, never from a model)."""
    if trigger.rule_id == "momentum_shift":
        return round(min(0.99, 0.5 + float(trigger.metrics.get("evidence_score", 0)) / 40), 2)
    if trigger.insight_type is InsightType.PLAYER_FORM:
        return 0.9
    return 1.0


def _insight(
    insight_id: str,
    trigger: Trigger,
    draft: InsightDraft,
    status: AuditStatus,
    svc: Services,
    actor: str,
    notes: str | None,
) -> Insight:
    return Insight(
        insight_id=insight_id,
        match_id=trigger.match_id,
        period=trigger.period,
        clock_ms=trigger.clock_ms,
        insight_type=trigger.insight_type,
        subject=trigger.subject,
        headline=draft.headline,
        explanation=draft.explanation,
        metrics={k: trigger.metrics[k] for k in draft.metric_keys if k in trigger.metrics},
        evidence_event_ids=list(draft.evidence_event_ids),
        confidence=confidence(trigger),
        audit_status=status,
        audit_notes=notes,
        created_by_agent=actor,
        model_version=svc.model_version,
        trigger_id=trigger.trigger_id,
    )


async def explain_and_audit(trigger: Trigger, facts: Facts, svc: Services) -> Insight:
    """Maker-checker loop: draft, audit, revise at most ``MAX_REVISIONS`` times."""
    insight_id = svc.ledger.next_id(svc.match_id, "I")
    narrate = trigger.insight_type is InsightType.RECAP
    actor = "narrator" if narrate else "explainer"
    request = ExplainRequest(
        task="narrate" if narrate else "explain",
        copy_key=copy.copy_key(trigger),
        trigger=trigger,
        facts=facts,
    )
    draft = template_draft(request)
    status = AuditStatus.BLOCKED
    notes = ""
    for round_no in range(1, MAX_REVISIONS + 2):
        try:
            draft = await draft_insight(svc.models, request)
            svc.audit(insight_id, trigger, round_no, actor, "draft", draft=draft)
        except ModelUnavailable as exc:
            draft = template_draft(request)
            svc.audit(
                insight_id, trigger, round_no, actor, "fallback", findings=[str(exc)], draft=draft
            )
        if round_no == 1 and trigger.trigger_id in svc.inject_error_for:
            draft, change = plant_error(draft, trigger.metrics)
            svc.audit(
                insight_id,
                trigger,
                round_no,
                "test",
                "inject_error",
                findings=[change],
                draft=draft,
            )
        findings = await svc.auditor.check(draft, trigger)
        svc.audit(insight_id, trigger, round_no, "auditor", "check", not findings, findings, draft)
        result = AuditResult(False, findings, "check")
        if not findings:
            result = await svc.auditor.review(draft, trigger, facts)
            svc.audit(
                insight_id,
                trigger,
                round_no,
                "auditor",
                "review",
                result.passed,
                result.findings or result.notes,
            )
        if result.passed:
            status = AuditStatus.APPROVED if round_no == 1 else AuditStatus.CORRECTED
            svc.audit(
                insight_id,
                trigger,
                round_no,
                "auditor",
                "approve" if round_no == 1 else "correct",
                True,
            )
            notes = (
                "Recomputed every metric from evidence: matches"
                if round_no == 1
                else f"Passed after {round_no - 1} revision(s); earlier drafts blocked"
            )
            break
        if round_no > MAX_REVISIONS:
            if result.findings and all(is_style(f) for f in result.findings):
                # Numbers and evidence pass; only the form does not. Air the template copy
                # (same numbers, house style) rather than block a sound insight.
                draft = template_draft(request)
                svc.audit(
                    insight_id, trigger, round_no, actor, "fallback", findings=result.findings,
                    draft=draft,
                )  # fmt: skip
                again = await svc.auditor.check(draft, trigger)
                svc.audit(insight_id, trigger, round_no, "auditor", "check", not again, again,
                          draft)  # fmt: skip
                if not again:
                    status = AuditStatus.CORRECTED
                    svc.audit(insight_id, trigger, round_no, "auditor", "correct", True)
                    notes = f"Template copy after {round_no - 1} revision(s) for style"
                    break
                result = AuditResult(False, again, "check")
            svc.audit(insight_id, trigger, round_no, "auditor", "block", False, result.findings)
            notes = "Blocked: " + "; ".join(result.findings)
            break
        svc.audit(insight_id, trigger, round_no, "auditor", "revise", False, result.findings)
        request = request.model_copy(update={"feedback": result.findings})
    insight = _insight(insight_id, trigger, draft, status, svc, actor, notes)
    svc.ledger.put_insight(insight)
    return insight


async def template_insight(trigger: Trigger, facts: Facts, svc: Services) -> Insight:
    """Low-risk path (speed alerts): template copy, code audit, no model calls."""
    insight_id = svc.ledger.next_id(svc.match_id, "I")
    request = ExplainRequest(copy_key=copy.copy_key(trigger), trigger=trigger, facts=facts)
    draft = template_draft(request)
    findings = await svc.auditor.check(draft, trigger)
    passed = not findings
    status = AuditStatus.APPROVED if passed else AuditStatus.BLOCKED
    svc.audit(insight_id, trigger, 1, "auditor", "check", passed, findings, draft)
    svc.audit(insight_id, trigger, 1, "auditor", "approve" if passed else "block", passed)
    insight = _insight(
        insight_id,
        trigger,
        draft,
        status,
        svc,
        "template",
        "Template copy; numbers recomputed" if passed else "; ".join(findings),
    )
    svc.ledger.put_insight(insight)
    return insight


def decide(insight: Insight, trigger: Trigger, svc: Services, producer: str | None) -> Insight:
    """Approval gate: auto-approve low-risk types; otherwise the producer decides.

    ``producer`` None leaves an audited insight pending the producer (it does not air).
    """
    if insight.audit_status is AuditStatus.BLOCKED:
        return insight
    if trigger.insight_type in AUTO_APPROVE:
        approved_by = f"auto-approval rule: {trigger.insight_type.value}"
    elif producer is not None:
        approved_by = producer
        svc.audit(insight.insight_id, trigger, 0, producer, "producer_approve", True)
    else:
        return insight
    updated = insight.model_copy(update={"approved_by": approved_by})
    svc.ledger.put_insight(updated)
    return updated


def producer_block(
    insight: Insight, trigger: Trigger, svc: Services, producer: str, reason: str | None
) -> Insight:
    """The producer keeps an audited insight off air."""
    why = f"Blocked by {producer}" + (f": {reason}" if reason else "")
    svc.audit(insight.insight_id, trigger, 0, producer, "producer_block", False, [why])
    updated = insight.model_copy(update={"audit_status": AuditStatus.BLOCKED, "audit_notes": why})
    svc.ledger.put_insight(updated)
    return updated


@dataclass(frozen=True)
class CopyRow:
    """One audience mode in one language, ready for an overlay."""

    mode: str
    language: str
    text: str
    source: str  # "model", "template" (model copy failed its check) or "auto" (no models)
    findings: tuple[str, ...] = ()


def airs(insight: Insight) -> bool:
    return insight.approved_by is not None and insight.audit_status is not AuditStatus.BLOCKED


async def personalize_modes(
    insight: Insight, trigger: Trigger, facts: Facts, svc: Services
) -> dict[str, CheckedCopy]:
    """English copy for every audience mode, from one Personalizer call (failing modes are
    asked again on their own, then fall back to the template)."""
    metrics = dict(trigger.metrics)  # all recomputed by the Auditor
    return await personalize(svc.models, copy.copy_key(trigger), svc.modes, insight, metrics, facts)


async def localize_mode(
    insight: Insight,
    trigger: Trigger,
    facts: Facts,
    svc: Services,
    mode: str,
    use_models: bool = True,
    english: CheckedCopy | None = None,
) -> list[CopyRow]:
    """One audience mode: its English copy (``english``, from ``personalize_modes``; or a
    Personalizer call for this mode alone) and one Translator call for the other
    languages."""
    key = copy.copy_key(trigger)
    metrics = dict(trigger.metrics)  # all recomputed by the Auditor
    others = [lang for lang in svc.languages if lang != "en"]
    if not use_models:
        values = {**metrics, **facts.names()}
        return [
            CopyRow(mode, lang, copy.mode_text(key, mode, lang, values), "auto")
            for lang in ("en", *others)
        ]
    if english is None:
        english = (await personalize(svc.models, key, [mode], insight, metrics, facts))[mode]
    done = (
        await translate(svc.models, key, mode, others, english.text, metrics, facts)
        if others
        else {}
    )
    return [
        CopyRow(mode, "en", english.text, english.source, tuple(english.findings)),
        *(
            CopyRow(mode, lang, done[lang].text, done[lang].source, tuple(done[lang].findings))
            for lang in others
        ),
    ]


def publish(
    insight: Insight, trigger: Trigger, svc: Services, rows: Iterable[CopyRow]
) -> list[Overlay]:
    """Overlays for every copy (in mode, then language order) and one fallback record."""
    order = {(m, lang): i for i, (m, lang) in enumerate(product(svc.modes, svc.languages))}
    overlays = []
    fallbacks = []
    for row in sorted(rows, key=lambda r: order.get((r.mode, r.language), len(order))):
        overlay = build_overlay(
            svc.ledger.next_id(svc.match_id, "O"),
            trigger,
            insight,
            row.mode,
            row.language,
            row.text,
        )
        svc.ledger.put_overlay(overlay)
        overlays.append(overlay)
        if row.source == "template":
            fallbacks.append(f"{row.mode}/{row.language}: " + "; ".join(row.findings))
    if fallbacks:
        svc.audit(insight.insight_id, trigger, 0, "copy_check", "fallback", findings=fallbacks)
    return overlays


async def tell_story(
    insight: Insight, trigger: Trigger, facts: Facts, svc: Services, story: StoryFacts
) -> StoryRecord:
    """The "Tell me more" beats for an aired insight, stored with one fallback record for
    the beats that aired as templates."""
    told = await tell(
        svc.models, copy.copy_key(trigger), insight, facts.signals, story, svc.languages
    )
    svc.ledger.put_story(told.record)
    if told.fallbacks:
        svc.audit(insight.insight_id, trigger, 0, "story_check", "fallback",
                  findings=told.fallbacks)  # fmt: skip
    return told.record


async def localize(
    insight: Insight, trigger: Trigger, facts: Facts, svc: Services, use_models: bool = True
) -> list[Overlay]:
    """Every audience mode in every language, as overlays (only for approved insights)."""
    if not airs(insight):
        return []
    english = await personalize_modes(insight, trigger, facts, svc) if use_models else {}
    rows = await asyncio.gather(
        *(
            localize_mode(insight, trigger, facts, svc, mode, use_models, english.get(mode))
            for mode in svc.modes
        )
    )
    return publish(insight, trigger, svc, (row for mode_rows in rows for row in mode_rows))
