"""Live service: the engine emits the batch triggers, and a live run ends like a replay."""

import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from pitch_ledger.agents.queue import InMemoryTriggerQueue
from pitch_ledger.cli import main
from pitch_ledger.engine.detector import detect
from pitch_ledger.eventstore import FileEventStore
from pitch_ledger.feed import InMemoryFeed, listen
from pitch_ledger.feed.live import feed_seconds, match_minute, stream_match
from pitch_ledger.feed.port import Delivered, FeedMessage
from pitch_ledger.ledger import InMemoryLedger
from pitch_ledger.live.engine import LiveMatch, rejected
from pitch_ledger.live.service import LiveOptions, LiveService
from pitch_ledger.matchlab.io import read_match
from pitch_ledger.push import RecordingNotifier
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


def test_a_late_or_repeated_reference_still_gives_the_batch_triggers(match_dir: Path) -> None:
    """A consumer that connects after the start gets rows before the reference (which the
    feed repeats); it keeps them, ignores repeats and still finds every trigger."""
    files = read_match(match_dir)
    reference = json.loads((match_dir / "match.json").read_text())
    live = LiveMatch("M0002")
    for n, (_, batch) in enumerate(feed_seconds(files.events, files.tracking)):
        if n % 600 == 20:  # first at second 20, then every 10 match minutes
            live.add("match", reference)
        for message in batch:
            assert not rejected(live.add(message.kind, message.row))
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


def test_stream_can_stage_a_feed_fault(match_dir: Path) -> None:
    feed, pauses = InMemoryFeed(), []
    report = stream_match(match_dir, feed, speed=1000, start_min=10, minutes=1, fault_at=10.5,
                          sleep=pauses.append)  # fmt: skip
    rows = [d.message.row for d in feed.messages(0.05)]
    [bad] = [r for r in rows if r.get("start_x") == 140.0]
    assert bad["event_type"] == "pass" and match_minute(bad) >= 10.5
    assert report.fault and report.fault.startswith(bad["event_id"]) and 12.0 in pauses


def test_live_run_ends_like_a_replay(match_dir: Path, tmp_path: Path) -> None:
    feed = InMemoryFeed()
    stream_match(match_dir, feed, speed=10_000, sleep=lambda s: None)
    ledger, store = InMemoryLedger(), FileEventStore(tmp_path / "store")
    options = LiveOptions(approve=True, inject_first_momentum=True, provider="offline",
                          tick_s=0.05, idle_s=0.5)  # fmt: skip
    push = RecordingNotifier()
    service = LiveService(feed, InMemoryTriggerQueue(), ledger, store, tmp_path / "live", options,
                          notifier=push)  # fmt: skip
    report = asyncio.run(service.run())
    groups = [g for g, _ in push.sent]
    # 3 shot-speed alerts are folded (2 into goals, 1 cooldown): stored, not aired
    assert groups.count("producers.M0002") == 15 and groups.count("fans.M0002") == 15
    assert ledger.alerts() == []  # a clean feed and run raise no ops alerts
    store.close()

    summary = report.as_dict()
    assert summary["matches"]["M0002"]["triggers"] == len(_batch(match_dir)) == 54
    assert summary["status"] == {"aired": 15, "pending": 0, "blocked": 0, "stored": 39}
    momentum = [i for i in ledger.insights("M0002") if i.insight_type.value == "momentum_shift"]
    assert [i.audit_status.value for i in momentum] == ["corrected"]  # planted error caught
    assert not summary["failures"]
    landed = tmp_path / "live" / "M0002"
    assert {p.name for p in landed.iterdir()} == {"match.json", "events.jsonl", "tracking.jsonl",
                                                  "triggers.jsonl"}  # fmt: skip
    counts = {p.stem: len(p.read_text().splitlines()) for p in (tmp_path / "store").iterdir()}
    assert counts["Triggers"] == 54 and counts["InsightStatus"] == 15
    assert counts["TeamState"] == 190 and counts["Events"] == 2418


class PausingFeed:
    """Feed rows from memory with one pause (a feed gap) before row ``pause_at``."""

    def __init__(self, messages: list[FeedMessage], pause_at: int, pause_s: float) -> None:
        self.items, self.pause_at, self.pause_s = messages, pause_at, pause_s

    def messages(self, idle_timeout_s: float) -> Iterator[Delivered]:
        for n, message in enumerate(self.items):
            if n == self.pause_at:
                time.sleep(self.pause_s)
            yield Delivered(message, time.time())

    def close(self) -> None:
        pass


def _opening(match_dir: Path, minutes: float) -> list[FeedMessage]:
    files = read_match(match_dir)
    reference = json.loads((match_dir / "match.json").read_text())
    rows = [FeedMessage("match", reference)]
    for _, batch in feed_seconds(files.events, files.tracking):
        rows += [m for m in batch if match_minute(m.row) < minutes]
    return rows


def test_feed_faults_raise_ops_alerts(match_dir: Path, tmp_path: Path) -> None:
    rows = _opening(match_dir, 3)
    bad_at = next(n for n, m in enumerate(rows) if m.kind == "event" and m.row["event_type"]
                  == "pass" and match_minute(m.row) > 1)  # fmt: skip
    bad_id = rows[bad_at].row["event_id"]
    rows[bad_at] = FeedMessage("event", {**rows[bad_at].row, "start_x": 140.0})  # off the pitch
    pause_at = next(n for n, m in enumerate(rows) if n and match_minute(m.row) > 2)
    before_gap = [m.row["event_id"] for m in rows[:pause_at]
                  if m.kind == "event" and m.row["event_id"] != bad_id][-1]  # fmt: skip
    ledger, push = InMemoryLedger(), RecordingNotifier()
    options = LiveOptions(provider="offline", tick_s=0.05, idle_s=0.3, gap_s=0.2)
    service = LiveService(PausingFeed(rows, pause_at, 1.0), InMemoryTriggerQueue(), ledger, None,
                          tmp_path / "live", options, notifier=push)  # fmt: skip
    report = asyncio.run(service.run())

    alerts = {a.kind: a for a in ledger.alerts("M0002")}
    assert set(alerts) == {"validation_reject", "feed_gap"}
    reject, gap = alerts["validation_reject"], alerts["feed_gap"]
    assert reject.refs == [bad_id] and "ROW-02" in reject.message
    assert gap.refs == [before_gap] and gap.message.startswith("No feed message for")
    assert report.matches["M0002"]["rejected"] == 1
    landed = (tmp_path / "live" / "M0002" / "events.jsonl").read_text()
    assert f'"event_id":"{bad_id}"' not in landed  # dropped before the match folder
    assert [m["event"] for g, m in push.sent if g == "producers.M0002"].count("alert_changed") == 2


def test_a_feed_joined_mid_match_raises_no_false_rejects(match_dir: Path) -> None:
    files = read_match(match_dir)
    live = LiveMatch("M0002")
    live.add("match", json.loads((match_dir / "match.json").read_text()))
    results = []
    for _, batch in feed_seconds(files.events, files.tracking):
        for m in batch:
            if match_minute(m.row) >= 60 and m.row.get("period") == 2:  # lineup changes before
                results += live.add(m.kind, m.row)
    assert results == [] and live.events and live.full_time
