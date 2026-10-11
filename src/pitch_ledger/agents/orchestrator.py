"""Orchestrator: an Agent Framework workflow that takes one trigger to its overlays.

::

    intake ─┬─ momentum, key moment, player form, recap ─> explain_audit ─┐
            ├─ speed alert ──────────────────────────────> template ──────┤
            ├─ producer decision ─────────────────────────────────────────┤
            └─ anything else ─> store (kept in the ledger only)           v
                                                                    approval gate
                                                                          │ approved
                                              personalize (all modes, one call)
            ┌────────────┬────────────┬─────────────┬─────────────┬───────┘
         analyst      casual     club_focus   player_focus  single_metric
            └────────────┴────────────┴──────┬──────┴─────────────┘
           (Translator: other languages,     v
            one call per mode)            publish (overlays to the ledger)

Explain/audit is the Explainer ⇄ Auditor maker-checker loop. The approval gate passes
speed alerts by rule and other insights only with a producer's approval; without one
they wait (``pending``). The Personalizer writes every mode's English copy in one call;
the five audience modes then translate concurrently. Each run yields one
``Outcome``. ``replay`` feeds a match's triggers through the trigger queue in match-clock
order (retries, then dead letter), as the live service will from Service Bus.
"""

import logging
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, Never

from agent_framework import (
    Case,
    Default,
    Executor,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)

from pitch_ledger.agents import copy
from pitch_ledger.agents.contracts import Facts
from pitch_ledger.agents.copy_check import CheckedCopy
from pitch_ledger.agents.facts import build_facts
from pitch_ledger.agents.flow import (
    AUTO_APPROVE,
    FULL_FLOW,
    CopyRow,
    Services,
    airs,
    decide,
    explain_and_audit,
    localize_mode,
    personalize_modes,
    producer_block,
    publish,
    template_insight,
)
from pitch_ledger.agents.queue import InMemoryTriggerQueue, TriggerQueue
from pitch_ledger.schema import Event, Insight, Match, Player, Team, Trigger
from pitch_ledger.schema.enums import AuditStatus

log = logging.getLogger(__name__)

Route = Literal["full", "auto", "stored"]
Status = Literal["stored", "pending", "blocked", "aired"]


@dataclass(frozen=True)
class MatchContext:
    """Reference rows the agents may name (teams, players) and events for focus players."""

    match: Match
    teams: Sequence[Team]
    players: Sequence[Player]
    events_by_id: dict[str, Event]

    def facts(self, trigger: Trigger) -> Facts:
        return build_facts(
            trigger,
            self.teams,
            self.players,
            self.match.home_team_id,
            self.match.away_team_id,
            self.events_by_id,
        )


# Messages between executors ---------------------------------------------------------


@dataclass(frozen=True)
class TriggerJob:
    trigger: Trigger


@dataclass(frozen=True)
class ProducerDecision:
    insight_id: str
    producer: str
    approve: bool = True
    reason: str | None = None


@dataclass(frozen=True)
class Audited:
    insight: Insight
    trigger: Trigger
    facts: Facts
    route: Route


@dataclass(frozen=True)
class LocalizeJob:
    insight: Insight
    trigger: Trigger
    facts: Facts
    route: Route
    english: Mapping[str, CheckedCopy] | None = None  # per mode, once personalized


@dataclass(frozen=True)
class ModeCopy:
    job: LocalizeJob
    rows: tuple[CopyRow, ...]


@dataclass(frozen=True)
class Outcome:
    trigger_id: str
    route: Route
    status: Status
    insight_id: str | None = None
    audit_status: str | None = None
    overlays: int = 0


# Executors ----------------------------------------------------------------------------


class _Step(Executor):
    def __init__(self, orchestrator: "Orchestrator", step_id: str) -> None:
        super().__init__(id=step_id)
        self.orch = orchestrator


class Intake(_Step):
    """Stores every trigger in the ledger; the switch-case edges route it."""

    @handler
    async def on_trigger(self, job: TriggerJob, ctx: WorkflowContext[TriggerJob]) -> None:
        self.orch.svc.ledger.save_trigger(job.trigger)
        await ctx.send_message(job)

    @handler
    async def on_decision(
        self, decision: ProducerDecision, ctx: WorkflowContext[ProducerDecision]
    ) -> None:
        await ctx.send_message(decision)


class Store(_Step):
    """Minor triggers: kept in the ledger, not explained."""

    @handler
    async def run(self, job: TriggerJob, ctx: WorkflowContext[Never, Outcome]) -> None:
        await ctx.yield_output(Outcome(job.trigger.trigger_id, "stored", "stored"))


class ExplainAudit(_Step):
    """Explainer (or Narrator) ⇄ Auditor, at most two revisions."""

    @handler
    async def run(self, job: TriggerJob, ctx: WorkflowContext[Audited]) -> None:
        facts = self.orch.context.facts(job.trigger)
        insight = await explain_and_audit(job.trigger, facts, self.orch.svc)
        await ctx.send_message(Audited(insight, job.trigger, facts, "full"))


class Template(_Step):
    """Speed alerts: template copy, code audit, no model calls."""

    @handler
    async def run(self, job: TriggerJob, ctx: WorkflowContext[Audited]) -> None:
        facts = self.orch.context.facts(job.trigger)
        insight = await template_insight(job.trigger, facts, self.orch.svc)
        await ctx.send_message(Audited(insight, job.trigger, facts, "auto"))


class ApprovalGate(_Step):
    """Auto-approval rule for speed alerts; otherwise the producer decides."""

    @handler
    async def on_audited(self, msg: Audited, ctx: WorkflowContext[LocalizeJob, Outcome]) -> None:
        insight = decide(msg.insight, msg.trigger, self.orch.svc, self.orch.producer)
        await self._forward(insight, msg.trigger, msg.facts, msg.route, ctx)

    @handler
    async def on_decision(
        self, msg: ProducerDecision, ctx: WorkflowContext[LocalizeJob, Outcome]
    ) -> None:
        ledger = self.orch.svc.ledger
        insight = ledger.get_insight(msg.insight_id)
        trigger = ledger.get_trigger(insight.trigger_id or "") if insight else None
        if insight is None or trigger is None:
            raise KeyError(f"no insight {msg.insight_id} with a stored trigger")
        route = self.orch.route(trigger)
        facts = self.orch.context.facts(trigger)
        if insight.approved_by is None and insight.audit_status is not AuditStatus.BLOCKED:
            if msg.approve:
                insight = decide(insight, trigger, self.orch.svc, msg.producer)
            else:
                insight = producer_block(insight, trigger, self.orch.svc, msg.producer, msg.reason)
            await self._forward(insight, trigger, facts, route, ctx)
        else:  # already decided: nothing changes
            await ctx.yield_output(_outcome(insight, trigger, route))

    async def _forward(
        self,
        insight: Insight,
        trigger: Trigger,
        facts: Facts,
        route: Route,
        ctx: WorkflowContext[LocalizeJob, Outcome],
    ) -> None:
        if airs(insight):
            await ctx.send_message(LocalizeJob(insight, trigger, facts, route))
        else:
            await ctx.yield_output(_outcome(insight, trigger, route))


class Personalize(_Step):
    """Personalizer: every mode's English copy in one call (full route only)."""

    @handler
    async def run(self, job: LocalizeJob, ctx: WorkflowContext[LocalizeJob]) -> None:
        english = None
        if job.route == "full":
            english = await personalize_modes(job.insight, job.trigger, job.facts, self.orch.svc)
        await ctx.send_message(replace(job, english=english))


class ModeWriter(_Step):
    """One audience mode: its English copy, then the Translator (other languages)."""

    def __init__(self, orchestrator: "Orchestrator", mode: str) -> None:
        super().__init__(orchestrator, f"mode_{mode}")
        self.mode = mode

    @handler
    async def run(self, job: LocalizeJob, ctx: WorkflowContext[ModeCopy]) -> None:
        rows = await localize_mode(
            job.insight,
            job.trigger,
            job.facts,
            self.orch.svc,
            self.mode,
            use_models=job.route == "full",
            english=job.english.get(self.mode) if job.english else None,
        )
        await ctx.send_message(ModeCopy(job, tuple(rows)))


class Publisher(_Step):
    """Overlay builder: every copy (mode and language) becomes an overlay in the ledger."""

    @handler
    async def run(self, parts: list[ModeCopy], ctx: WorkflowContext[Never, Outcome]) -> None:
        job = parts[0].job
        overlays = publish(
            job.insight, job.trigger, self.orch.svc, (row for p in parts for row in p.rows)
        )
        await ctx.yield_output(
            _outcome(job.insight, job.trigger, job.route, status="aired", overlays=len(overlays))
        )


def _outcome(
    insight: Insight,
    trigger: Trigger,
    route: Route,
    status: Status | None = None,
    overlays: int = 0,
) -> Outcome:
    if status is None:
        status = "aired" if airs(insight) else "pending"
        if insight.audit_status is AuditStatus.BLOCKED:
            status = "blocked"
    return Outcome(
        trigger.trigger_id, route, status, insight.insight_id, insight.audit_status.value, overlays
    )


# Orchestrator -----------------------------------------------------------------------------


@dataclass
class ReplayReport:
    outcomes: list[Outcome] = field(default_factory=list)
    seconds: dict[str, float] = field(default_factory=dict)
    dead_letters: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, dict[str, int]]:
        processed = [o for o in self.outcomes if o.route != "stored"]
        return {
            "routes": dict(Counter(o.route for o in self.outcomes)),
            "status": dict(Counter(o.status for o in processed)),
            "audit_status": dict(Counter(o.audit_status or "" for o in processed)),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts(),
            "overlays": sum(o.overlays for o in self.outcomes),
            "outcomes": [
                asdict(o) | {"seconds": round(self.seconds.get(o.trigger_id, 0.0), 3)}
                for o in self.outcomes
            ],
            "dead_letters": self.dead_letters,
        }


def clock_order(triggers: Iterable[Trigger]) -> list[Trigger]:
    return sorted(triggers, key=lambda t: (t.period, t.clock_ms, t.trigger_id))


def first_momentum(triggers: Iterable[Trigger]) -> str | None:
    """The trigger that ``--inject-error`` targets (the demo's key moment)."""
    return next(
        (t.trigger_id for t in clock_order(triggers) if t.rule_id == "momentum_shift"), None
    )


class Orchestrator:
    def __init__(
        self,
        svc: Services,
        context: MatchContext,
        producer: str | None = None,
        only: Iterable[str] | None = None,
    ) -> None:
        """``producer`` approves every audited insight (the demo's ``--approve``); None
        leaves them pending. ``only`` limits processing to these insight types or rule
        ids (others are stored), to cap paid model calls."""
        self.svc = svc
        self.context = context
        self.producer = producer
        self.only = frozenset(only) if only is not None else None
        self.workflow = self._build()

    def route(self, trigger: Trigger) -> Route:
        selected = self.only is None or {trigger.insight_type.value, trigger.rule_id} & self.only
        if not selected or not copy.supported(copy.copy_key(trigger)):
            return "stored"
        if trigger.context.get("superseded_by"):
            return "stored"  # folded into another moment, a cooldown or the speed-badge cap
        if trigger.insight_type in FULL_FLOW:
            return "full"
        if trigger.insight_type in AUTO_APPROVE:
            return "auto"
        return "stored"

    def _build(self) -> Workflow:
        intake = Intake(self, "intake")
        explain = ExplainAudit(self, "explain_audit")
        template = Template(self, "template")
        store = Store(self, "store")
        gate = ApprovalGate(self, "approval_gate")
        personalizer = Personalize(self, "personalize")
        modes = [ModeWriter(self, mode) for mode in self.svc.modes]
        publisher = Publisher(self, "publish")

        def routed(name: Route) -> Any:
            return lambda m: isinstance(m, TriggerJob) and self.route(m.trigger) == name

        return (
            WorkflowBuilder(name="pitch-ledger-insight", start_executor=intake, output_from="all")
            .add_switch_case_edge_group(
                intake,
                [
                    Case(condition=lambda m: isinstance(m, ProducerDecision), target=gate),
                    Case(condition=routed("full"), target=explain),
                    Case(condition=routed("auto"), target=template),
                    Default(target=store),
                ],
            )
            .add_edge(explain, gate)
            .add_edge(template, gate)
            .add_edge(gate, personalizer)
            .add_fan_out_edges(personalizer, modes)
            .add_fan_in_edges(modes, publisher)
            .build()
        )

    async def run(self, message: TriggerJob | ProducerDecision) -> Outcome:
        result = await self.workflow.run(message)
        outcomes = [o for o in result.get_outputs() if isinstance(o, Outcome)]
        if len(outcomes) != 1:
            raise RuntimeError(f"expected one outcome, got {len(outcomes)}")
        return outcomes[0]

    async def process(self, trigger: Trigger) -> Outcome:
        return await self.run(TriggerJob(trigger))

    async def decide(
        self, insight_id: str, producer: str, approve: bool = True, reason: str | None = None
    ) -> Outcome:
        """A producer's later decision on a pending insight (the Studio console)."""
        return await self.run(ProducerDecision(insight_id, producer, approve, reason))

    async def replay(
        self, triggers: Iterable[Trigger], queue: TriggerQueue | None = None
    ) -> ReplayReport:
        """Every trigger through the queue in match-clock order; failures retry, then
        dead-letter."""
        queue = queue or InMemoryTriggerQueue()
        for trigger in clock_order(triggers):
            queue.send(trigger)
        report = ReplayReport()
        while (delivery := queue.receive()) is not None:
            start = time.perf_counter()
            try:
                outcome = await self.process(delivery.trigger)
            except Exception as exc:  # retried by the queue, then dead-lettered
                log.warning("%s failed: %s", delivery.message_id, exc)
                queue.abandon(delivery, f"{type(exc).__name__}: {exc}")
                continue
            queue.complete(delivery)
            report.outcomes.append(outcome)
            report.seconds[outcome.trigger_id] = time.perf_counter() - start
        report.dead_letters = [
            {"trigger_id": d.message_id, "deliveries": d.delivery_count, "errors": d.errors}
            for d in queue.dead_letters()
        ]
        return report
