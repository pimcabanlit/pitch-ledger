"""Replay a match directory through the orchestrator: the local stand-in for the live service.

The detector runs over the match feed (writing ``triggers.jsonl``, which the MCP stats
server reads), then every trigger goes through the queue and the insight workflow. The
ledger lands in ``<match_dir>/ledger/`` with a ``run.json`` summary (provider, counts,
model usage, timings).
"""

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
    ReplayReport,
    clock_order,
    first_momentum,
)
from pitch_ledger.engine.detector import detect
from pitch_ledger.ledger import FileLedger, InMemoryLedger
from pitch_ledger.matchlab.io import read_match, write_triggers
from pitch_ledger.mcp_server.client import LocalStats, McpStats
from pitch_ledger.mcp_server.tools import StatsTools
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


async def replay_match(match_dir: Path, options: ReplayOptions | None = None) -> ReplayRun:
    options = options or ReplayOptions()
    files = read_match(match_dir)
    events = [Event.model_validate(e) for e in files.events]
    tracking = [TrackingSample.model_validate(s) for s in files.tracking]
    triggers = clock_order(detect(files.match, events, tracking))
    write_triggers(match_dir, triggers)
    context = MatchContext(files.match, files.teams, files.players, {e.event_id: e for e in events})
    match_id = files.match.match_id
    ledger_dir = options.ledger_dir or match_dir / "ledger"
    ledger = FileLedger(ledger_dir, fresh=True) if options.write else InMemoryLedger()
    started = datetime.now(UTC)
    clock = time.perf_counter()
    async with AsyncExitStack() as stack:
        models = await stack.enter_async_context(ModelManager(_settings(options)))
        stats: LocalStats | McpStats
        if options.stats == "mcp":
            stats = await stack.enter_async_context(McpStats(match_dir))
        else:
            stats = LocalStats(StatsTools(match_dir))
        svc = Services(
            match_id, models, Auditor(models, stats), ledger, injected(triggers, options.inject)
        )
        producer = DEMO_PRODUCER if options.approve else None
        orchestrator = Orchestrator(svc, context, producer, options.only)
        report = await orchestrator.replay(triggers)
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
        "usage": models.usage.as_dict(),
        **report.as_dict(),
    }
    run = ReplayRun(match_id, triggers, ledger, report, summary)
    if isinstance(ledger, FileLedger):
        ledger.flush()
        (ledger_dir / RUN_FILE).write_text(json.dumps(summary, indent=2) + "\n", "utf-8")
        run.ledger_dir = ledger_dir
    return run
