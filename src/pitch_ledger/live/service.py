"""The live service: feed -> engine -> trigger queue -> agents -> ledger, with the event store
written along the way.

Two concurrent loops share one process:

- **ingest** moves feed messages (read on a thread) into each ``LiveMatch``, lands them
  under ``out_dir/<match>/`` (a match directory the stats tools can read), writes raw rows
  and minute snapshots to the event store, and every ``tick_s`` emits settled triggers to
  the queue (and to ``triggers.jsonl`` and the store).
- **worker** takes triggers off the queue and runs the orchestrator over a snapshot of the
  match so far; outcomes go to the ledger, insight status to the store.

The queue, ledger, store and feed are interfaces (Service Bus, Cosmos DB, ADX, Event Hubs
in Azure; in-memory or files locally), so this module never imports a cloud SDK.
"""

import asyncio
import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pitch_ledger.agents.auditor import Auditor
from pitch_ledger.agents.flow import FULL_FLOW, Services
from pitch_ledger.agents.model_manager import ModelManager, ModelSettings
from pitch_ledger.agents.orchestrator import MatchContext, Orchestrator, Outcome
from pitch_ledger.agents.queue import Delivery, TriggerQueue
from pitch_ledger.eventstore.port import EventStore
from pitch_ledger.eventstore.rows import FeedHealthMeter, insight_status_row, trigger_row
from pitch_ledger.feed.port import Delivered, FeedSubscriber
from pitch_ledger.ledger import FileLedger, InMemoryLedger
from pitch_ledger.live.engine import LiveMatch
from pitch_ledger.matchlab.io import EVENTS_JSONL, MATCH_FILE, TRACKING_JSONL, TRIGGERS_JSONL
from pitch_ledger.mcp_server.client import LocalStats
from pitch_ledger.mcp_server.tools import StatsTools
from pitch_ledger.push import Notifier, NullNotifier
from pitch_ledger.schema.enums import InsightType

log = logging.getLogger(__name__)
DEMO_PRODUCER = "producer_demo"
STATUSES = ("aired", "pending", "blocked", "stored")


@dataclass(frozen=True)
class LiveOptions:
    approve: bool = False
    inject_first_momentum: bool = False
    provider: Literal["offline", "foundry"] | None = None
    budget: int | None = None
    only: tuple[str, ...] | None = None
    settle_s: float = 60.0
    tick_s: float = 5.0
    idle_s: float = 30.0


@dataclass
class LiveReport:
    matches: dict[str, dict[str, int]] = field(default_factory=dict)
    outcomes: list[Outcome] = field(default_factory=list)
    trigger_to_outcome_s: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        waits = sorted(self.trigger_to_outcome_s.values())
        return {
            "matches": self.matches,
            "processed": len(self.outcomes),
            "status": {s: sum(o.status == s for o in self.outcomes) for s in STATUSES},
            "trigger_to_outcome_s": {
                "p50": round(waits[len(waits) // 2], 2) if waits else None,
                "max": round(waits[-1], 2) if waits else None,
            },
            "failures": self.failures,
            "usage": self.usage,
        }  # fmt: skip


class LiveService:
    def __init__(
        self,
        subscriber: FeedSubscriber,
        trigger_queue: TriggerQueue,
        ledger: InMemoryLedger,
        store: EventStore | None,
        out_dir: Path,
        options: LiveOptions | None = None,
        clock: Callable[[], float] = time.monotonic,
        notifier: Notifier | None = None,
    ) -> None:
        self.subscriber, self.queue, self.ledger, self.store = (
            subscriber,
            trigger_queue,
            ledger,
            store,
        )
        self.out_dir, self.options, self.clock = out_dir, options or LiveOptions(), clock
        self.matches: dict[str, LiveMatch] = {}
        self.report = LiveReport()
        self.meter = FeedHealthMeter()
        self.notifier: Notifier = notifier or NullNotifier()
        self._inbox: queue.Queue[Delivered | None] = queue.Queue()
        self._ingest_done = asyncio.Event()
        self._emitted_at: dict[str, float] = {}
        self._pending = 0
        self._injected: set[str] = set()
        self._handles: dict[tuple[str, str], Any] = {}

    # Feed thread -----------------------------------------------------------------
    def _pump(self) -> None:
        try:
            for delivered in self.subscriber.messages(self.options.idle_s):
                self._inbox.put(delivered)
                m = delivered.message
                if (
                    m.kind == "event"
                    and m.row.get("event_type") == "period_end"
                    and m.row.get("period") == 2
                ):
                    break  # full time: stop reading; the ingest loop finishes the match
        finally:
            self._inbox.put(None)

    # Ingest ----------------------------------------------------------------------
    def _land(self, match_id: str, kind: str, row: dict[str, Any]) -> None:
        folder = self.out_dir / match_id
        folder.mkdir(parents=True, exist_ok=True)
        if kind == "match":
            (folder / MATCH_FILE).write_text(
                json.dumps(row, indent=2, ensure_ascii=False) + "\n", "utf-8"
            )
            return
        name = EVENTS_JSONL if kind == "event" else TRACKING_JSONL
        key = (match_id, kind)
        if key not in self._handles:
            self._handles[key] = (folder / name).open("w", encoding="utf-8", newline="\n")
        fh = self._handles[key]
        fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        fh.flush()

    def _take(self, delivered: Delivered) -> None:
        m = delivered.message
        live = self.matches.setdefault(m.match_id, LiveMatch(m.match_id))
        live.add(m.kind, m.row)
        self._land(m.match_id, m.kind, m.row)
        counts = self.report.matches.setdefault(
            m.match_id, {"events": 0, "tracking": 0, "triggers": 0}
        )
        if m.kind in ("event", "tracking"):
            counts["events" if m.kind == "event" else "tracking"] += 1
            self.meter.add(m.match_id, delivered.received_at, delivered.latency_ms)
            if self.store is not None:
                self.store.append("Events" if m.kind == "event" else "Tracking", [m.row])

    async def _tick(self, final: bool = False) -> None:
        for live in self.matches.values():
            settle_ms = int(self.options.settle_s * 1000)
            triggers = await asyncio.to_thread(live.settled_triggers, settle_ms, final)
            if live.reference is not None and live.emitted:
                path = self.out_dir / live.match_id / TRIGGERS_JSONL
                path.write_text("".join(t.model_dump_json() + "\n" for t in live.emitted), "utf-8")
            for trigger in triggers:
                if (
                    self.options.inject_first_momentum
                    and not self._injected
                    and trigger.insight_type is InsightType.MOMENTUM_SHIFT
                ):
                    self._injected.add(trigger.trigger_id)
                await asyncio.to_thread(self.queue.send, trigger)
                self._emitted_at[trigger.trigger_id] = self.clock()
                self._pending += 1
                self.report.matches[live.match_id]["triggers"] += 1
            if self.store is not None:
                if triggers:
                    self.store.append("Triggers", [trigger_row(t) for t in triggers])
                if snapshots := await asyncio.to_thread(live.team_state_rows):
                    self.store.append("TeamState", snapshots)
        if self.store is not None:
            if finished := self.meter.take(before=time.time() if not final else float("inf")):
                self.store.append("FeedHealth", finished)
            self.store.flush()

    async def ingest(self) -> None:
        last_tick = self.clock()
        done = False
        while not done:
            while True:
                try:
                    item = self._inbox.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    done = True
                    break
                self._take(item)
            if done or self.clock() - last_tick >= self.options.tick_s:
                await self._tick(final=done)
                last_tick = self.clock()
            if not done:
                await asyncio.sleep(0.1)
        for fh in self._handles.values():
            fh.close()
        self._ingest_done.set()

    # Worker ----------------------------------------------------------------------
    async def _process(self, models: ModelManager, delivery: Delivery) -> Outcome:
        trigger = delivery.trigger
        live = self.matches[trigger.match_id]
        files = live.files()
        tools = StatsTools.snapshot(
            self.out_dir / live.match_id,
            files,
            list(live.events),
            list(live.tracking),
            list(live.emitted),
        )
        stats = LocalStats(tools)
        inject = {trigger.trigger_id} & self._injected
        svc = Services(live.match_id, models, Auditor(models, stats), self.ledger, inject)
        context = MatchContext(files.match, files.teams, files.players,
                               {e.event_id: e for e in live.events})  # fmt: skip
        producer = DEMO_PRODUCER if self.options.approve else None
        return await Orchestrator(svc, context, producer, self.options.only).process(trigger)

    async def worker(self, models: ModelManager) -> None:
        idle_since: float | None = None
        while True:
            delivery = await asyncio.to_thread(self.queue.receive)
            if delivery is None:
                if self._ingest_done.is_set():
                    if self._pending <= 0:
                        return
                    idle_since = idle_since or self.clock()
                    if self.clock() - idle_since >= self.options.idle_s:  # never hang
                        self.report.failures.append(
                            f"{self._pending} triggers not processed (queue empty for "
                            f"{self.options.idle_s:g} s; see the dead-letter queue)"
                        )
                        return
                await asyncio.sleep(0.2)
                continue
            idle_since = None
            try:
                outcome = await self._process(models, delivery)
            except Exception as exc:  # retried by the queue, then dead-lettered
                log.warning("%s failed: %s", delivery.message_id, exc)
                self.report.failures.append(f"{delivery.message_id}: {type(exc).__name__}: {exc}")
                await asyncio.to_thread(
                    self.queue.abandon, delivery, f"{type(exc).__name__}: {exc}"
                )
                if delivery.delivery_count >= 3:
                    self._pending -= 1
                continue
            await asyncio.to_thread(self.queue.complete, delivery)
            self._pending -= 1
            self.report.outcomes.append(outcome)
            started = self._emitted_at.get(outcome.trigger_id)
            if started is not None:
                self.report.trigger_to_outcome_s[outcome.trigger_id] = self.clock() - started
            insight = self.ledger.get_insight(outcome.insight_id or "")
            if insight is not None:
                signal = {"insight_id": insight.insight_id, "status": outcome.status}
                self.notifier.notify(insight.match_id, "producers", "insight_changed", signal)
                if outcome.status == "aired":
                    self.notifier.notify(insight.match_id, "fans", "overlays_changed", signal)
            if insight is not None and self.store is not None:
                self.store.append(
                    "InsightStatus", [insight_status_row(insight, datetime.now(UTC).timestamp())]
                )
                self.store.flush()
            if isinstance(self.ledger, FileLedger):
                self.ledger.flush()

    async def run(self) -> LiveReport:
        thread = threading.Thread(target=self._pump, daemon=True)
        thread.start()
        overrides: dict[str, Any] = {}
        if self.options.provider is not None:
            overrides["model_provider"] = self.options.provider
        if self.options.budget is not None:
            overrides["model_call_budget"] = self.options.budget
        settings = ModelSettings(**overrides)
        async with ModelManager(settings) as models:
            await asyncio.gather(self.ingest(), self.worker(models))
            self.report.usage = models.usage.as_dict()
        return self.report


__all__ = ["FULL_FLOW", "LiveOptions", "LiveReport", "LiveService"]
