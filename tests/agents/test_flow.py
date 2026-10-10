"""Explainer -> Auditor maker-checker, approval gate and localization (planted errors)."""

import asyncio

import pytest

from pitch_ledger.agents.auditor import Auditor
from pitch_ledger.agents.contracts import InsightDraft, ReviewVerdict
from pitch_ledger.agents.flow import Services, decide, explain_and_audit, localize, template_insight
from pitch_ledger.agents.model_manager import ModelManager, ModelSettings
from pitch_ledger.ledger import InMemoryLedger
from pitch_ledger.ops import OpsDesk, review_insight
from pitch_ledger.schema import Insight, Trigger
from pitch_ledger.validation.insight_rules import unsupported_numbers
from tests.agents.conftest import Case
from tests.agents.fakes import ScriptedChatClient, SimStats


def _case(cases: list[Case], match_id: str, rule: str) -> Case:
    return next(c for c in cases if c.trigger.match_id == match_id and c.trigger.rule_id == rule)


def _services(case: Case, clients: dict[str, ScriptedChatClient] | None = None,
              trigger: Trigger | None = None, inject: bool = False) -> Services:  # fmt: skip
    models = ModelManager(ModelSettings(), clients=dict(clients or {}))
    original = case.trigger
    stats = SimStats(case.sim, [original], case.tracking)
    inject_for = {(trigger or original).trigger_id} if inject else set()
    return Services(case.trigger.match_id, models, Auditor(models, stats), InMemoryLedger(),
                    inject_error_for=inject_for)  # fmt: skip


def _actions(svc: Services, insight: Insight) -> list[str]:
    return [a.action for a in svc.ledger.audits(insight.insight_id)]


def test_offline_momentum_insight_approved_first_time(cases: list[Case]) -> None:
    case = _case(cases, "M0002", "momentum_shift")
    svc = _services(case)
    insight = asyncio.run(explain_and_audit(case.trigger, case.facts, svc))
    assert insight.audit_status == "approved"
    assert _actions(svc, insight) == ["draft", "check", "review", "approve"]
    assert set(insight.metrics) <= set(case.trigger.metrics)
    assert set(insight.evidence_event_ids) <= set(case.trigger.evidence_event_ids)
    text = f"{insight.headline} {insight.explanation}"
    assert unsupported_numbers(text, insight.metrics.values()) == []
    assert insight.trigger_id == case.trigger.trigger_id and insight.approved_by is None


def test_planted_error_caught_then_corrected(cases: list[Case]) -> None:
    """The demo's key moment: an over-reaching number is blocked, the revision passes."""
    case = _case(cases, "M0002", "momentum_shift")
    svc = _services(case, inject=True)
    insight = asyncio.run(explain_and_audit(case.trigger, case.facts, svc))
    assert insight.audit_status == "corrected"
    trail = svc.ledger.audits(insight.insight_id)
    assert [a.action for a in trail] == ["draft", "inject_error", "check", "revise", "draft",
                                         "check", "review", "correct"]  # fmt: skip
    wrong = trail[1]
    failed = trail[2]
    assert failed.passed is False and failed.findings[0].startswith("INS-01")
    assert wrong.explanation != insight.explanation
    assert "turnovers_after" in wrong.findings[0]
    assert unsupported_numbers(insight.explanation, insight.metrics.values()) == []


def _wrong_draft(trigger: Trigger) -> str:
    draft = InsightDraft(
        headline="Collapse",
        explanation="They lost the ball 99 times.",
        metric_keys=["turnovers_after"],
        evidence_event_ids=trigger.evidence_event_ids[:2],
    )
    return draft.model_dump_json()


def test_repeated_wrong_number_is_blocked_after_two_revisions(cases: list[Case]) -> None:
    case = _case(cases, "M0002", "momentum_shift")
    explainer = ScriptedChatClient([_wrong_draft(case.trigger)] * 3)
    svc = _services(case, {"explainer": explainer})
    insight = asyncio.run(explain_and_audit(case.trigger, case.facts, svc))
    assert insight.audit_status == "blocked"
    actions = _actions(svc, insight)
    assert actions.count("draft") == 3 and actions.count("revise") == 2 and actions[-1] == "block"
    assert explainer.requests[1]["feedback"] and explainer.requests[2]["feedback"]
    assert "99" in explainer.requests[1]["feedback"][0]
    held = decide(insight, case.trigger, svc, "producer_demo")
    assert held.approved_by is None
    assert asyncio.run(localize(held, case.trigger, case.facts, svc)) == []
    [alert] = review_insight(OpsDesk(svc.ledger), insight.insight_id)  # the producer is told
    assert alert.kind == "insight_blocked" and alert.message.endswith("It will not air.")


def test_unknown_metric_and_stray_evidence_are_findings(cases: list[Case]) -> None:
    case = _case(cases, "M0002", "momentum_shift")
    svc = _services(case)
    draft = InsightDraft(headline="h", explanation="Control fell.", metric_keys=["made_up"],
                         evidence_event_ids=["M0002-E999999"])  # fmt: skip
    findings = asyncio.run(svc.auditor.check(draft, case.trigger))
    assert any("made_up" in f for f in findings)
    assert any("M0002-E999999" in f for f in findings)


def test_tampered_trigger_metric_fails_recompute(cases: list[Case]) -> None:
    case = _case(cases, "M0002", "momentum_shift")
    tampered = case.trigger.model_copy(
        update={"metrics": {**case.trigger.metrics, "control_after": 55}}
    )
    svc = _services(case, trigger=tampered)
    insight = asyncio.run(explain_and_audit(tampered, case.facts, svc))
    assert insight.audit_status == "blocked"
    findings = svc.ledger.audits(insight.insight_id)[1].findings
    assert any(f.startswith("recompute: control_after") for f in findings)


def test_review_can_send_back_wording(cases: list[Case]) -> None:
    case = _case(cases, "M0005", "player_form")
    reviewer = ScriptedChatClient([
        ReviewVerdict(verdict="revise", issues=["claims he is injured"]).model_dump_json(),
        ReviewVerdict(verdict="pass").model_dump_json(),
    ])  # fmt: skip
    svc = _services(case, {"auditor": reviewer})
    insight = asyncio.run(explain_and_audit(case.trigger, case.facts, svc))
    assert insight.audit_status == "corrected"
    assert _actions(svc, insight) == ["draft", "check", "review", "revise", "draft", "check",
                                      "review", "correct"]  # fmt: skip


def test_localize_five_modes_three_languages(cases: list[Case]) -> None:
    case = _case(cases, "M0002", "momentum_shift")
    svc = _services(case)
    insight = asyncio.run(explain_and_audit(case.trigger, case.facts, svc))
    assert asyncio.run(localize(insight, case.trigger, case.facts, svc)) == []  # not approved yet
    approved = decide(insight, case.trigger, svc, "producer_demo")
    assert approved.approved_by == "producer_demo"
    overlays = asyncio.run(localize(approved, case.trigger, case.facts, svc))
    assert len(overlays) == 15
    assert {(o.audience_mode.value, o.language) for o in overlays} == {
        (m, lang) for m in ("analyst", "casual", "club_focus", "player_focus", "single_metric")
        for lang in ("en", "es", "fil")
    }  # fmt: skip
    for o in overlays:
        assert o.template == "momentum_bar" and o.priority == 2
        assert unsupported_numbers(o.text, case.trigger.metrics.values(), o.language) == []
    assert overlays[0].data["control"] == [case.trigger.metrics["control_before"],
                                           case.trigger.metrics["control_after"]]  # fmt: skip


def test_translator_number_change_falls_back_to_template(cases: list[Case]) -> None:
    case = _case(cases, "M0002", "momentum_shift")
    translator = ScriptedChatClient(['{"text": "Control 999"}'] * 20)
    svc = _services(case, {"translator": translator})
    insight = decide(asyncio.run(explain_and_audit(case.trigger, case.facts, svc)), case.trigger,
                     svc, "producer_demo")  # fmt: skip
    overlays = asyncio.run(localize(insight, case.trigger, case.facts, svc))
    translated = [o for o in overlays if o.language != "en"]
    assert len(translated) == 10 and all("999" not in o.text for o in translated)
    fallback = [a for a in svc.ledger.audits(insight.insight_id) if a.action == "fallback"]
    assert len(fallback) == 1 and len(fallback[0].findings) == 10
    assert all("999" in f for f in fallback[0].findings)


def test_speed_alert_auto_path_uses_no_model(cases: list[Case]) -> None:
    case = _case(cases, "M0002", "shot_speed")
    svc = _services(case)
    insight = asyncio.run(template_insight(case.trigger, case.facts, svc))
    approved = decide(insight, case.trigger, svc, None)
    assert approved.approved_by == "auto-approval rule: speed_alert"
    overlays = asyncio.run(localize(approved, case.trigger, case.facts, svc, use_models=False))
    assert len(overlays) == 15 and all(o.template == "speed_badge" for o in overlays)
    assert svc.models.usage.calls == 0


@pytest.mark.parametrize("rule", ["goal", "sending_off", "half_time_recap", "full_time_recap"])
def test_other_full_flow_rules_pass_audit(cases: list[Case], rule: str) -> None:
    case = next(c for c in cases if c.trigger.rule_id == rule)
    svc = _services(case)
    insight = asyncio.run(explain_and_audit(case.trigger, case.facts, svc))
    assert insight.audit_status == "approved", svc.ledger.audits(insight.insight_id)
    if rule.endswith("recap"):
        assert insight.created_by_agent == "narrator"
