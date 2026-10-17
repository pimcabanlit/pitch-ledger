"""The "Tell me more" story: facts, template beats, story checks and the Narrator step."""

import asyncio
import json

from pitch_ledger.agents import copy
from pitch_ledger.agents.auditor import Auditor
from pitch_ledger.agents.flow import Services, tell_story
from pitch_ledger.agents.model_manager import ModelManager, ModelSettings
from pitch_ledger.agents.story import (
    DEPTHS,
    LANGUAGES,
    StoryFacts,
    beat_problems,
    story_facts,
    template_beats,
)
from pitch_ledger.agents.storyteller import KEYS, tell
from pitch_ledger.ledger import InMemoryLedger
from pitch_ledger.schema import Insight, Trigger
from tests.agents.conftest import Case
from tests.agents.fakes import ScriptedChatClient, SimStats


def _insight(trigger: Trigger) -> Insight:
    return Insight(insight_id=f"{trigger.match_id}-I0001", match_id=trigger.match_id,
                   period=trigger.period, clock_ms=trigger.clock_ms,
                   insight_type=trigger.insight_type, subject=trigger.subject,
                   headline="A moment", explanation="Something happened.",
                   metrics=dict(trigger.metrics), evidence_event_ids=trigger.evidence_event_ids,
                   confidence=0.9, created_by_agent="explainer", model_version="offline",
                   trigger_id=trigger.trigger_id, approved_by="producer_demo")  # fmt: skip


def _facts(case: Case) -> tuple[Insight, StoryFacts]:
    insight = _insight(case.trigger)
    sim = case.sim
    return insight, story_facts(sim.match, sim.teams, sim.players, sim.events, insight,
                                case.trigger)  # fmt: skip


def _case(cases: list[Case], rule: str) -> Case:
    return next(c for c in cases if c.trigger.match_id == "M0002" and c.trigger.rule_id == rule)


def _services(case: Case, clients: dict[str, ScriptedChatClient] | None = None) -> Services:
    models = ModelManager(ModelSettings(), clients=dict(clients or {}))
    stats = SimStats(case.sim, [case.trigger], case.tracking, case.season)
    return Services(case.trigger.match_id, models, Auditor(models, stats), InMemoryLedger())


def test_template_beats_pass_the_story_checks_for_every_demo_trigger(cases: list[Case]) -> None:
    """The templates are what airs when a model beat fails, so they must pass themselves."""
    written = 0
    for case in cases:
        if not copy.supported(copy.copy_key(case.trigger)):
            continue
        _, facts = _facts(case)
        for depth in DEPTHS:
            for language in LANGUAGES:
                for beat, text in template_beats(facts, depth, language).items():
                    where = (case.trigger.trigger_id, depth, language, beat, text)
                    if text:
                        assert beat_problems(text, facts, depth, language) == [], where
                        written += 1
    assert written > 500


def test_the_buildup_only_uses_what_came_before_the_moment(cases: list[Case]) -> None:
    case = _case(cases, "momentum_shift")
    insight, facts = _facts(case)
    later = [g for g in facts.goals_all if g.period > insight.period]
    assert later == []  # nothing after the moment
    numbers = template_beats(facts, "numbers", "en")["buildup"]
    plain = template_beats(facts, "plain", "en")["buildup"]
    assert "control score" in numbers and "%" in numbers
    assert "control score" not in plain and "%" not in plain


def test_offline_story_is_the_template_beats_checked(cases: list[Case]) -> None:
    case = _case(cases, "momentum_shift")
    insight, facts = _facts(case)
    svc = _services(case)
    told = asyncio.run(tell(svc.models, copy.copy_key(case.trigger), insight, case.facts.signals,
                            facts, LANGUAGES))  # fmt: skip
    assert told.fallbacks == []
    assert {(b.depth, b.language) for b in told.record.beats} == {
        (d, lang) for d in DEPTHS for lang in LANGUAGES
    }
    for beats in told.record.beats:
        templates = template_beats(facts, beats.depth, beats.language)
        assert (beats.buildup, beats.outlook) == (templates["buildup"], templates["outlook"])
        assert set(beats.sources.values()) == {"model"}
    assert svc.models.usage.by_role == {"narrator": 1, "translator": 1}


def test_a_beat_with_an_invented_number_falls_back_to_its_template(cases: list[Case]) -> None:
    case = _case(cases, "momentum_shift")
    insight, facts = _facts(case)
    good = {f"{d}.{b}": t for d in DEPTHS for b, t in template_beats(facts, d, "en").items()}
    bad = dict(good)
    bad["plain.outlook"] = "They need 99 more passes to break a record."
    bad["numbers.buildup"] = "Shots 99-1 before the moment."
    narrator = ScriptedChatClient([
        json.dumps({"texts": bad}),  # two beats fail
        json.dumps({"texts": {"plain.outlook": bad["plain.outlook"],
                              "numbers.buildup": good["numbers.buildup"]}}),  # one fixed
    ])  # fmt: skip
    svc = _services(case, {"narrator": narrator})
    record = asyncio.run(tell_story(insight, case.trigger, case.facts, svc, facts))

    assert [sorted(r["drafts"]) for r in narrator.requests] == [
        sorted(KEYS), ["numbers.buildup", "plain.outlook"]]  # fmt: skip
    plain_en = record.beats_for("plain", "en")
    assert plain_en is not None
    assert plain_en.outlook == template_beats(facts, "plain", "en")["outlook"]
    assert plain_en.sources == {"buildup": "model", "outlook": "template"}
    numbers_en = record.beats_for("numbers", "en")
    assert numbers_en is not None and numbers_en.sources["buildup"] == "model"
    assert svc.ledger.story(insight.insight_id) == record
    [audit] = svc.ledger.audits(insight.insight_id)
    assert (audit.actor, audit.action) == ("story_check", "fallback")
    [finding] = audit.findings
    assert finding.startswith("plain_outlook/en: numbers not in metrics: 99")
    assert "unsupported claim 'record'" in finding


def test_story_survives_the_models_being_unavailable(cases: list[Case]) -> None:
    case = _case(cases, "momentum_shift")
    insight, facts = _facts(case)
    down = ScriptedChatClient([RuntimeError("down")] * 2)
    svc = _services(case, {"narrator": down})
    record = asyncio.run(tell_story(insight, case.trigger, case.facts, svc, facts))
    for beats in record.beats:
        templates = template_beats(facts, beats.depth, beats.language)
        assert (beats.buildup, beats.outlook) == (templates["buildup"], templates["outlook"])
    english = [b for b in record.beats if b.language == "en"]
    assert all(set(b.sources.values()) == {"template"} for b in english)
    [audit] = svc.ledger.audits(insight.insight_id)
    assert all("model unavailable" in f for f in audit.findings) and len(audit.findings) == 4
