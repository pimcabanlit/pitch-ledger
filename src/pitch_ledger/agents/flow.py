"""The stages one trigger goes through, each writing to the ledger and its audit trail.

1. ``explain_and_audit``: Explainer (or Narrator) drafts; the Auditor checks; findings go
   back at most twice; the insight ends ``approved``, ``corrected`` or ``blocked``.
2. ``decide``: the producer (or the auto-approval rule for low-risk speed alerts)
   approves or holds an audited insight.
3. ``localize``: the Personalizer writes five audience modes and the Translator renders each
   in the other languages, concurrently; every copy passes a number check or falls back
   to template copy; the Overlay builder turns each into an overlay.
"""

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field

from pitch_ledger.agents import copy
from pitch_ledger.agents.auditor import MAX_REVISIONS, Auditor, AuditResult
from pitch_ledger.agents.contracts import ExplainRequest, Facts, InsightDraft
from pitch_ledger.agents.explainer import draft_insight, plant_error, template_draft
from pitch_ledger.agents.model_manager import ModelManager, ModelUnavailable
from pitch_ledger.agents.overlay_builder import build_overlay
from pitch_ledger.agents.personalizer import personalize
from pitch_ledger.agents.translator import translate
from pitch_ledger.ledger.repository import Ledger
from pitch_ledger.schema import AuditRecord, Insight, Overlay, Trigger
from pitch_ledger.schema.audit import AuditAction
from pitch_ledger.schema.enums import AuditStatus, InsightType

FULL_FLOW = frozenset(
    {InsightType.MOMENTUM_SHIFT, InsightType.KEY_MOMENT, InsightType.PLAYER_FORM, InsightType.RECAP}
)
"""Insight types that go through Explainer, Auditor, producer and personalization."""
AUTO_APPROVE = frozenset({InsightType.SPEED_ALERT})
"""Low-risk types: template copy, code audit, auto-approved, no model calls."""
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
        s = self.models.settings
        if s.model_provider == "offline":
            return "offline-templates"
        return f"foundry:{s.model_strong}|{s.model_small}"

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


async def localize(
    insight: Insight, trigger: Trigger, facts: Facts, svc: Services, use_models: bool = True
) -> list[Overlay]:
    """Every audience mode in every language, as overlays (only for approved insights)."""
    if insight.approved_by is None or insight.audit_status is AuditStatus.BLOCKED:
        return []
    key = copy.copy_key(trigger)
    metrics = dict(trigger.metrics)  # all recomputed by the Auditor
    values = {**metrics, **facts.names()}

    async def one_mode(mode: str) -> list[tuple[str, str, str, str]]:
        if use_models:
            english = await personalize(svc.models, key, mode, insight, metrics, facts)
            text, source = english.text, english.source
        else:
            text, source = copy.mode_text(key, mode, "en", values), "template"
        out = [(mode, "en", text, source)]
        others = [lang for lang in svc.languages if lang != "en"]
        if use_models:
            done = await asyncio.gather(
                *(translate(svc.models, key, mode, lang, text, metrics, facts) for lang in others)
            )
            out += [(mode, lang, c.text, c.source) for lang, c in zip(others, done, strict=True)]
        else:
            out += [
                (mode, lang, copy.mode_text(key, mode, lang, values), "template") for lang in others
            ]
        return out

    results = await asyncio.gather(*(one_mode(mode) for mode in svc.modes))
    overlays = []
    fallbacks = []
    for mode, language, text, source in (row for rows in results for row in rows):
        overlay = build_overlay(
            svc.ledger.next_id(svc.match_id, "O"), trigger, insight, mode, language, text
        )
        svc.ledger.put_overlay(overlay)
        overlays.append(overlay)
        if source == "template" and use_models:
            fallbacks.append(f"{mode}/{language}")
    if fallbacks:
        svc.audit(
            insight.insight_id,
            trigger,
            0,
            "copy_check",
            "fallback",
            findings=[f"template copy used for {', '.join(fallbacks)}"],
        )
    return overlays
