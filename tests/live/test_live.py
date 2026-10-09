"""Live service: the engine emits the batch triggers, and a live run ends like a replay."""

import asyncio
import json
from pathlib import Path

import pytest

from pitch_ledger.agents.queue import InMemoryTriggerQueue
from pitch_ledger.cli import main
from pitch_ledger.engine.detector import detect
from pitch_ledger.eventstore import FileEventStore
from pitch_ledger.feed import InMemoryFeed, listen
from pitch_ledger.feed.live import feed_seconds, stream_match
from pitch_ledger.ledger import InMemoryLedger
from pitch_ledger.live.engine import LiveMatch
from pitch_ledger.live.service import LiveOptions, LiveService
from pitch_ledger.matchlab.io import read_match
from pitch_ledger.schema import Event, TrackingSample, Trigger


@pytest.fixture(scope="module")
def match_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("data") / "M0002"
    assert main(["generate", "--scenario", "late_collapse", "--seed", "4212",
                 "--out", str(out)]) == 0  # fmt: skip
    return out


def _batch(match_dir: Path) -> list[Trigger]:
    files = read_match(match_dir)
    events = [Event.model_validate(e) for e in files.events]
    tracking = [TrackingSample.model_validate(s) for s in files.tracking]
    return detect(files.match, events, tracking)


def _key(t: Trigger) -> tuple[object, ...]:
    return (t.trigger_id, t.rule_id, t.period, t.clock_ms, t.subject, t.metrics,
            t.evidence_event_ids)  # fmt: skip


def test_live_engine_emits_exactly_the_batch_triggers(match_dir: Path) -> None:
    files = read_match(match_dir)
    live = LiveMatch("M0002")
    live.add("match", json.loads((match_dir / "match.json").read_text()))
    for n, (_, batch) in enumerate(feed_seconds(files.events, files.tracking)):
        for message in batch:
            live.add(message.kind, message.row)
        if n % 30 == 0:
            for t in live.settled_triggers(60_000):
                assert t.clock_ms + 60_000 <= live.position()[1] or t.period < live.position()[0]
    assert live.full_time
    live.settled_triggers(60_000, final=True)
    assert [_key(t) for t in live.emitted] == [_key(t) for t in _batch(match_dir)]


def test_stream_sends_the_match_reference_first_and_listen_lands_it(
    match_dir: Path, tmp_path: Path
) -> None:
    feed = InMemoryFeed()
    stream_match(match_dir, feed, speed=1000, start_min=10, minutes=0.1,
                 sleep=lambda s: None)  # fmt: skip
    first = next(iter(feed.messages(0.05)))
    assert first.message.kind == "match" and first.message.match_id == "M0002"
    stream_match(match_dir, feed, speed=1000, minutes=0.1, sleep=lambda s: None)
    report = listen(feed, tmp_path, idle_timeout_s=0.05)
    assert report.received["M0002/match"] == 1
    landed = json.loads((tmp_path / "M0002" / "match.json").read_text())
    assert landed == json.loads((match_dir / "match.json").read_text())


def test_live_run_ends_like_a_replay(match_dir: Path, tmp_path: Path) -> None:
    feed = InMemoryFeed()
    stream_match(match_dir, feed, speed=10_000, sleep=lambda s: None)
    ledger, store = InMemoryLedger(), FileEventStore(tmp_path / "store")
    options = LiveOptions(approve=True, inject_first_momentum=True, provider="offline",
                          tick_s=0.05, idle_s=0.5)  # fmt: skip
    service = LiveService(feed, InMemoryTriggerQueue(), ledger, store, tmp_path / "live", options)
    report = asyncio.run(service.run())
    store.close()

    summary = report.as_dict()
    assert summary["matches"]["M0002"]["triggers"] == len(_batch(match_dir)) == 56
    assert summary["status"] == {"aired": 18, "pending": 0, "blocked": 0, "stored": 38}
    momentum = [i for i in ledger.insights("M0002") if i.insight_type.value == "momentum_shift"]
    assert [i.audit_status.value for i in momentum] == ["corrected"]  # planted error caught
    assert not summary["failures"]
    landed = tmp_path / "live" / "M0002"
    assert {p.name for p in landed.iterdir()} == {"match.json", "events.jsonl", "tracking.jsonl",
                                                  "triggers.jsonl"}  # fmt: skip
    counts = {p.stem: len(p.read_text().splitlines()) for p in (tmp_path / "store").iterdir()}
    assert counts["Triggers"] == 56 and counts["InsightStatus"] == 18
    assert counts["TeamState"] == 190 and counts["Events"] == 2418
