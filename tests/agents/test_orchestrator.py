"""Orchestrator workflow and match replay: routing, approval gate, queue, end to end."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pitch_ledger.agents import copy
from pitch_ledger.agents.auditor import Auditor
from pitch_ledger.agents.flow import AUTO_APPROVE, FULL_FLOW, Services
from pitch_ledger.agents.model_manager import ModelManager, ModelSettings
from pitch_ledger.agents.orchestrator import MatchContext, Orchestrator
from pitch_ledger.agents.replay import ReplayOptions, replay_match
from pitch_ledger.ledger import InMemoryLedger
from pitch_ledger.matchlab.io import write_match
from pitch_ledger.schema import InsightSubject, Trigger
from pitch_ledger.validation.insight_rules import unsupported_numbers
from tests.agents.conftest import Case
from tests.agents.fakes import SimStats


@pytest.fixture(scope="module")
def m0002(cases: list[Case]) -> list[Case]:
    return [c for c in cases if c.trigger.match_id == "M0002"]


@pytest.fixture(scope="module")
def m0002_dir(m0002: list[Case], tmp_path_factory: pytest.TempPathFactory) -> Path:
    case = m0002[0]
    out = tmp_path_factory.mktemp("data") / "M0002"
    sim = case.sim
    write_match(out, sim.match, sim.teams, sim.players, sim.events, case.tracking)
    return out


def _orchestrator(m0002: list[Case], stats: Any = None, **kwargs: Any) -> Orchestrator:
    sim = m0002[0].sim
    models = ModelManager(ModelSettings())
    stats = stats or SimStats(sim, [c.trigger for c in m0002], m0002[0].tracking)
    svc = Services("M0002", models, Auditor(models, stats), InMemoryLedger())
    context = MatchContext(sim.match, sim.teams, sim.players, {e.event_id: e for e in sim.events})
    return Orchestrator(svc, context, **kwargs)


def _trigger(m0002: list[Case], rule: str, superseded: bool = False) -> Trigger:
    """The first trigger of ``rule`` (by default one the detector did not supersede)."""
    return next(
        c.trigger
        for c in m0002
        if c.trigger.rule_id == rule and bool(c.trigger.context.get("superseded_by")) == superseded
    )


def test_replay_m0002_end_to_end_over_mcp(m0002_dir: Path, tmp_path: Path) -> None:
    options = ReplayOptions(approve=True, inject="first", ledger_dir=tmp_path / "ledger")
    run = asyncio.run(replay_match(m0002_dir, options))
    s = run.summary
    expected_full = sum(
        t.insight_type in FULL_FLOW and copy.supported(copy.copy_key(t)) for t in run.triggers
    )
    expected_auto = sum(
        t.insight_type in AUTO_APPROVE and not t.context.get("superseded_by") for t in run.triggers
    )
    # folded speed alerts: 2 on goals, 1 cooldown, 1 speed-badge cap; badges that are not a
    # first or a new best (and every brace or hat-trick) are folded too, and only badges fold
    folded = [t for t in run.triggers if t.context.get("superseded_by")]
    assert sum(t.insight_type.value == "speed_alert" for t in folded) == 4
    assert {t.insight_type for t in folded} <= AUTO_APPROVE
    assert s["counts"]["routes"]["full"] == expected_full >= 8
    assert s["counts"]["routes"]["auto"] == expected_auto
    assert s["counts"]["status"] == {"aired": expected_full + expected_auto}
    assert s["dead_letters"] == [] and s["stats"] == "mcp"
    assert s["stats_calls"] == expected_full + expected_auto  # one recompute per trigger
    assert s["overlays"] == 15 * (expected_full + expected_auto)

    # The planted error is caught and corrected; the corrected insight airs.
    ledger = run.ledger
    momentum = next(i for i in ledger.insights() if i.insight_type.value == "momentum_shift")
    assert momentum.trigger_id in s["inject_error_for"]
    assert momentum.audit_status.value == "corrected"
    assert [a.action for a in ledger.audits(momentum.insight_id)] == [
        "draft", "inject_error", "check", "revise", "draft", "check", "review", "correct",
        "producer_approve",
    ]  # fmt: skip
    assert momentum.approved_by == "producer_demo"

    # Every overlay of every aired insight: 5 modes x 3 languages, numbers from the trigger.
    for insight in ledger.insights():
        trigger = ledger.get_trigger(insight.trigger_id or "")
        assert trigger is not None
        overlays = ledger.overlays(insight.insight_id)
        assert len({(o.audience_mode, o.language) for o in overlays}) == 15
        for o in overlays:
            assert unsupported_numbers(o.text, trigger.metrics.values(), o.language) == []

    names = {p.name for p in (tmp_path / "ledger").iterdir()}
    assert names == {"triggers.jsonl", "insights.jsonl", "audits.jsonl", "overlays.jsonl",
                     "alerts.jsonl", "run.json"}  # fmt: skip
    assert ledger.alerts() == [] and s["ops_alerts"] == 0  # nothing blocked, no fallbacks
    assert json.loads((tmp_path / "ledger" / "run.json").read_text())["overlays"] == s["overlays"]
    assert (m0002_dir / "triggers.jsonl").exists()


def test_routing_and_only(m0002: list[Case]) -> None:
    orch = _orchestrator(m0002)
    assert orch.route(_trigger(m0002, "momentum_shift")) == "full"
    assert orch.route(_trigger(m0002, "goal")) == "full"
    assert orch.route(_trigger(m0002, "shot_speed")) == "auto"
    assert orch.route(_trigger(m0002, "shot_speed", superseded=True)) == "stored"
    limited = _orchestrator(m0002, only=["momentum_shift"])
    assert limited.route(_trigger(m0002, "momentum_shift")) == "full"
    assert limited.route(_trigger(m0002, "goal")) == "stored"


def test_minor_trigger_is_stored_not_explained(m0002: list[Case]) -> None:
    orch = _orchestrator(m0002)
    trigger = Trigger(
        trigger_id="M0002-T0099",
        match_id="M0002",
        period=1,
        clock_ms=60_000,
        insight_type="milestone",
        rule_id="pass_milestone",
        subject=InsightSubject(player_id="T01-P04"),
        metrics={"completed_passes": 75},
        evidence_event_ids=[],
        context={"superseded_by": "cap"},
    )  # fmt: skip  (not a team first)
    outcome = asyncio.run(orch.process(trigger))
    assert (outcome.route, outcome.status, outcome.insight_id) == ("stored", "stored", None)
    assert orch.svc.ledger.get_trigger("M0002-T0099") == trigger
    assert orch.svc.ledger.insights() == [] and orch.svc.models.usage.calls == 0


def test_pending_until_the_producer_decides(m0002: list[Case]) -> None:
    orch = _orchestrator(m0002)
    ledger = orch.svc.ledger

    outcome = asyncio.run(orch.process(_trigger(m0002, "momentum_shift")))
    assert (outcome.route, outcome.status, outcome.overlays) == ("full", "pending", 0)
    assert outcome.insight_id is not None

    approved = asyncio.run(orch.decide(outcome.insight_id, "producer_kim"))
    assert (approved.status, approved.overlays) == ("aired", 15)
    insight = ledger.get_insight(outcome.insight_id)
    assert insight is not None and insight.approved_by == "producer_kim"
    assert any(
        (a.actor, a.action) == ("producer_kim", "producer_approve")
        for a in ledger.audits(outcome.insight_id)
    )

    again = asyncio.run(orch.decide(outcome.insight_id, "producer_lee"))
    assert (again.status, again.overlays) == ("aired", 0)  # already decided: unchanged
    assert len(ledger.overlays(outcome.insight_id)) == 15

    goal = asyncio.run(orch.process(_trigger(m0002, "goal")))
    assert goal.insight_id is not None
    blocked = asyncio.run(orch.decide(goal.insight_id, "producer_kim", False, "wrong scorer"))
    assert (blocked.status, blocked.overlays) == ("blocked", 0)
    trail = ledger.audits(goal.insight_id)
    assert trail[-1].action == "producer_block" and "wrong scorer" in trail[-1].findings[0]


def test_speed_alert_airs_without_producer(m0002: list[Case]) -> None:
    orch = _orchestrator(m0002)
    outcome = asyncio.run(orch.process(_trigger(m0002, "shot_speed")))
    assert (outcome.route, outcome.status, outcome.overlays) == ("auto", "aired", 15)
    assert orch.svc.models.usage.calls == 0


class _BrokenStats:
    calls = 0

    async def recompute_trigger(self, trigger_id: str) -> dict[str, int | float]:
        raise ConnectionError("stats server gone")

    async def get_events(self, event_ids: list[str]) -> list[dict[str, Any]]:
        raise ConnectionError("stats server gone")


def test_failures_retry_then_dead_letter(m0002: list[Case]) -> None:
    orch = _orchestrator(m0002, stats=_BrokenStats(), producer="producer_demo")
    momentum = _trigger(m0002, "momentum_shift")
    report = asyncio.run(orch.replay([momentum]))
    assert report.outcomes == []
    assert report.dead_letters == [
        {"trigger_id": momentum.trigger_id, "deliveries": 3,
         "errors": ["ConnectionError: stats server gone"] * 3}
    ]  # fmt: skip


@pytest.fixture(scope="module")
def spans() -> InMemorySpanExporter:
    """Collect the spans the process emits (the global provider can be set only once)."""
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def test_each_trigger_and_decision_is_one_traced_span(
    m0002: list[Case], spans: InMemorySpanExporter
) -> None:
    orch = _orchestrator(m0002)
    spans.clear()
    outcome = asyncio.run(orch.process(_trigger(m0002, "goal")))
    assert outcome.insight_id is not None
    asyncio.run(orch.decide(outcome.insight_id, "producer_kim"))
    finished = {s.name: s for s in spans.get_finished_spans()}
    root = finished["insight goal"]
    attrs = dict(root.attributes or {})
    assert attrs["pitch_ledger.rule_id"] == "goal" and attrs["pitch_ledger.status"] == "pending"
    assert attrs["pitch_ledger.insight_id"] == outcome.insight_id
    check = finished["audit check (code)"]
    assert check.parent is not None and check.context.trace_id == root.context.trace_id
    assert dict(check.attributes or {})["pitch_ledger.passed"] is True
    decision = dict(finished["producer decision"].attributes or {})
    assert decision["pitch_ledger.decision"] == "approve"
    assert decision["pitch_ledger.status"] == "aired"
    assert "producer_kim" not in str(decision)  # the person stays in the ledger, not traces

    # Trace viewers show the moment as the input and the result as the output.
    [inp] = json.loads(str(attrs["gen_ai.input.messages"]))
    assert inp["role"] == "user" and inp["parts"][0]["content"].startswith("goal at ")
    [out] = json.loads(str(attrs["gen_ai.output.messages"]))
    insight = orch.svc.ledger.get_insight(outcome.insight_id)
    assert insight is not None and out["parts"][0]["content"] == f"pending: {insight.headline}"
    [aired] = json.loads(str(decision["gen_ai.output.messages"]))
    assert aired["parts"][0]["content"].endswith("(15 overlays)")
