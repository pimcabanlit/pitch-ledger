"""Replay a match directory through the orchestrator: the local stand-in for the live service.

The detector runs over the match feed (writing ``triggers.jsonl``, which the MCP stats
server reads), then every trigger goes through the queue and the insight workflow. The
ledger lands in ``<match_dir>/ledger/`` with a ``run.json`` summary (provider, counts,
model usage, timings). Ops alerts for blocked insights, template fallbacks and dead letters
go to the same ledger.
"""

import contextlib
import json
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pitch_ledger.agents.auditor import Auditor
from pitch_ledger.agents.flow import FULL_FLOW, Services
from pitch_ledger.agents.model_manager import ModelManager, ModelSettings
from pitch_ledger.agents.orchestrator import (
    MatchContext,
    Orchestrator,
    Outcome,
    ReplayReport,
    clock_order,
    first_momentum,
)
from pitch_ledger.agents.queue import TriggerQueue
from pitch_ledger.engine.detector import detect
from pitch_ledger.history import HistoryStore, MatchNotFinished, build_record, season_for
from pitch_ledger.ledger import FileLedger, InMemoryLedger
from pitch_ledger.matchlab.io import read_match, write_triggers
from pitch_ledger.mcp_server.client import LocalStats, McpStats
from pitch_ledger.mcp_server.tools import StatsTools
from pitch_ledger.ops import OpsDesk, dead_lettered, review_insight
from pitch_ledger.push import Notifier
from pitch_ledger.schema import Event, TrackingSample, Trigger

DEMO_PRODUCER = "producer_demo"
RUN_FILE = "run.json"

Inject = Literal["none", "first", "all"]


@dataclass(frozen=True)
class ReplayOptions:
    approve: bool = False
    """Approve every audited insight as ``producer_demo`` (otherwise they wait)."""
    inject: Inject = "none"
    """Plant a wrong number in the first draft: of the first momentum insight, or of all
    full-flow insights (evaluation)."""
    provider: Literal["offline", "foundry"] | None = None
    stats: Literal["mcp", "local"] = "mcp"
    only: tuple[str, ...] | None = None
    budget: int | None = None
    ledger_dir: Path | None = None
    """Where the ledger is written (default ``<match_dir>/ledger``)."""
    write: bool = True


@dataclass
class ReplayRun:
    match_id: str
    triggers: list[Trigger]
    ledger: InMemoryLedger
    report: ReplayReport
    summary: dict[str, Any] = field(default_factory=dict)
    ledger_dir: Path | None = None


def injected(triggers: list[Trigger], inject: Inject) -> set[str]:
    if inject == "first":
        first = first_momentum(triggers)
        return {first} if first else set()
    if inject == "all":
        return {t.trigger_id for t in triggers if t.insight_type in FULL_FLOW}
    return set()


def _settings(options: ReplayOptions) -> ModelSettings:
    overrides: dict[str, Any] = {}
    if options.provider is not None:
        overrides["model_provider"] = options.provider
    if options.budget is not None:
        overrides["model_call_budget"] = options.budget
    return ModelSettings(**overrides)


async def replay_match(
    match_dir: Path,
    options: ReplayOptions | None = None,
    ledger: InMemoryLedger | None = None,
    queue: TriggerQueue | None = None,
    history: HistoryStore | None = None,
) -> ReplayRun:
    """``ledger`` and ``queue`` replace the local file ledger and in-memory queue (the CLI
    passes the Cosmos DB and Service Bus adapters). With ``history``, goals and the full-time
    recap carry season facts from earlier matches, and the match's own record is kept there
    afterwards."""
    options = options or ReplayOptions()
    files = read_match(match_dir)
    events = [Event.model_validate(e) for e in files.events]
    tracking = [TrackingSample.model_validate(s) for s in files.tracking]
    season = season_for(files.match, history)
    triggers = clock_order(detect(files.match, events, tracking, season=season))
    write_triggers(match_dir, triggers)
    context = MatchContext(files.match, files.teams, files.players, {e.event_id: e for e in events})
    match_id = files.match.match_id
    ledger_dir = options.ledger_dir or match_dir / "ledger"
    if ledger is None:
        ledger = FileLedger(ledger_dir, fresh=True) if options.write else InMemoryLedger()
    started = datetime.now(UTC)
    clock = time.perf_counter()
    async with AsyncExitStack() as stack:
        models = await stack.enter_async_context(ModelManager(_settings(options)))
        stats: LocalStats | McpStats
        if options.stats == "mcp":
            stats = await stack.enter_async_context(McpStats(match_dir, season))
        else:
            stats = LocalStats(StatsTools(match_dir, season))
        svc = Services(
            match_id, models, Auditor(models, stats), ledger, injected(triggers, options.inject)
        )
        producer = DEMO_PRODUCER if options.approve else None
        orchestrator = Orchestrator(svc, context, producer, options.only)
        report = await orchestrator.replay(triggers, queue)
    desk = OpsDesk(ledger)
    for outcome in report.outcomes:
        review_insight(desk, outcome.insight_id)
    by_id = {t.trigger_id: t for t in triggers}
    for dead in report.dead_letters:
        if (trigger := by_id.get(dead["trigger_id"])) is not None:
            dead_lettered(desk, trigger, dead["deliveries"], dead["errors"])
    summary = {
        "match_id": match_id,
        "started_at": started.isoformat(timespec="seconds"),
        "duration_s": round(time.perf_counter() - clock, 2),
        "provider": models.provider,
        "model_version": svc.model_version,
        "stats": options.stats,
        "stats_calls": stats.calls,
        "producer": producer,
        "inject": options.inject,
        "inject_error_for": sorted(svc.inject_error_for),
        "only": list(options.only) if options.only else None,
        "triggers": len(triggers),
        "season_matches": season.match_ids if season else None,
        "usage": models.usage.as_dict(),
        "ops_alerts": len(ledger.alerts(match_id)),
        **report.as_dict(),
    }
    if history is not None:
        with contextlib.suppress(MatchNotFinished):  # a partial match has no result to keep
            history.put(build_record(files.match, events, tracking))
    run = ReplayRun(match_id, triggers, ledger, report, summary)
    if isinstance(ledger, FileLedger):
        ledger.flush()
    if options.write:
        ledger_dir.mkdir(parents=True, exist_ok=True)
        (ledger_dir / RUN_FILE).write_text(json.dumps(summary, indent=2) + "\n", "utf-8")
        run.ledger_dir = ledger_dir
    return run


async def decide_insight(
    match_dir: Path,
    ledger: InMemoryLedger,
    insight_id: str,
    producer: str,
    approve: bool,
    reason: str | None = None,
    provider: Literal["offline", "foundry"] | None = None,
    stats: Literal["mcp", "local"] = "local",
    notifier: Notifier | None = None,
) -> Outcome:
    """A producer's decision on a pending insight (the Studio console): approve runs the
    Personalizer and Translator and stores the overlays; block records the block. An insight
    that is already decided is left as it is. Template fallbacks raise an ops alert (signalled
    through ``notifier``). The ledger is flushed if it is a file ledger."""
    files = read_match(match_dir, with_tracking=False)
    events = [Event.model_validate(e) for e in files.events]
    context = MatchContext(files.match, files.teams, files.players, {e.event_id: e for e in events})
    async with AsyncExitStack() as stack:
        models = await stack.enter_async_context(
            ModelManager(_settings(ReplayOptions(provider=provider)))
        )
        gateway: LocalStats | McpStats
        if stats == "mcp":
            gateway = await stack.enter_async_context(McpStats(match_dir))
        else:
            gateway = LocalStats(StatsTools(match_dir))
        svc = Services(files.match.match_id, models, Auditor(models, gateway), ledger)
        outcome = await Orchestrator(svc, context).decide(insight_id, producer, approve, reason)
    review_insight(OpsDesk(ledger, notifier=notifier), insight_id)
    if isinstance(ledger, FileLedger):
        ledger.flush()
    return outcome
