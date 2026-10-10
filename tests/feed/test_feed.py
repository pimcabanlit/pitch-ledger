"""Live feed: local adapters, live-pace streaming and the listener."""

import json
from pathlib import Path
from typing import Any

import pytest

from pitch_ledger.cli import main
from pitch_ledger.feed import FeedMessage, FilePublisher, FileSubscriber, InMemoryFeed, listen
from pitch_ledger.feed.live import feed_seconds, latency_summary, match_minute, stream_match
from pitch_ledger.matchlab.io import read_match


@pytest.fixture(scope="module")
def match_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("data") / "M0007"
    assert main(["generate", "--seed", "77", "--home", "T03", "--away", "T05",
                 "--out", str(out)]) == 0  # fmt: skip
    return out


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _row(period: int, clock_ms: int, **extra: Any) -> dict[str, Any]:
    return {"match_id": "M0001", "period": period, "clock_ms": clock_ms, **extra}


def test_feed_seconds_orders_rows_and_skips_the_break() -> None:
    events = [_row(1, 1500, event_type="pass"), _row(1, 47 * 60_000, event_type="period_end"),
              _row(2, 0, event_type="period_start")]  # fmt: skip
    tracking = [_row(1, 1500, player_id="P1"), _row(1, 0, player_id="P1")]
    seconds = feed_seconds(events, tracking)
    assert [s for s, _ in seconds] == [0, 1, 47 * 60, 47 * 60 + 1]  # period 2 right after
    second_one = seconds[1][1]
    assert [m.kind for m in second_one] == ["event", "tracking"]  # event first at one ms
    assert sum(len(batch) for _, batch in seconds) == 5


def test_stream_paces_one_batch_per_match_second(match_dir: Path) -> None:
    feed, clock = InMemoryFeed(), FakeClock()
    report = stream_match(match_dir, feed, speed=4, start_min=60, minutes=1,
                          clock=clock, sleep=clock.sleep)  # fmt: skip
    assert report.batches == 60
    assert report.wall_s == pytest.approx(59 / 4)
    assert all(s == pytest.approx(0.25) for s in clock.sleeps)
    files = read_match(match_dir)
    in_window = [r for r in files.events if 60 <= match_minute(r) < 61]
    assert report.events == len(in_window)
    assert report.tracking == sum(60 <= match_minute(r) < 61 for r in files.tracking)


def test_stream_repeats_the_reference_for_late_consumers(match_dir: Path) -> None:
    feed, clock = InMemoryFeed(), FakeClock()
    stream_match(match_dir, feed, speed=4, start_min=60, minutes=1, reference_every_s=5,
                 clock=clock, sleep=clock.sleep)  # fmt: skip
    kinds = [d.message.kind for d in feed.messages(0.05)]
    assert kinds[0] == "match" and kinds.count("match") == 3  # at 0 s, then 5 s and 10 s


def test_stream_rejects_non_positive_speed(match_dir: Path) -> None:
    with pytest.raises(ValueError):
        stream_match(match_dir, InMemoryFeed(), speed=0)


def test_rows_land_unchanged_and_listen_stops_at_full_time(match_dir: Path, tmp_path: Path) -> None:
    feed, clock = InMemoryFeed(), FakeClock()
    stream_match(match_dir, feed, speed=1000, with_tracking=False, clock=clock, sleep=clock.sleep)
    report = listen(feed, tmp_path, idle_timeout_s=0.05)
    assert report.full_time
    landed = [json.loads(x) for x in (tmp_path / "M0007" / "events.jsonl").read_text().splitlines()]
    assert landed == read_match(match_dir, with_tracking=False).events
    assert report.as_dict()["latency"]["event"]["count"] == len(landed)


def test_file_feed_tails_only_new_lines_and_waits_for_whole_lines(tmp_path: Path) -> None:
    path = tmp_path / "feed.jsonl"
    publisher = FilePublisher(path, clock=lambda: 5.0)
    publisher.publish([FeedMessage("event", _row(1, 0, event_type="pass"))])
    subscriber = FileSubscriber(path)  # starts after the first message: skips it
    publisher.publish([FeedMessage("tracking", _row(1, 1000, player_id="P1"))])
    with path.open("a") as fh:
        fh.write('{"kind": "event", "sent_at": 6.0, "row": {"match_id": "M0001"')  # mid-line
        fh.flush()
        got = list(subscriber.messages(idle_timeout_s=0.2))
        assert [d.message.kind for d in got] == ["tracking"]
        assert got[0].message.sent_at == 5.0
        fh.write(', "period": 1, "clock_ms": 2000}}\n{"kind": "other", "row": {}}\n')
    got = list(subscriber.messages(idle_timeout_s=0.2))
    assert [d.message.row["clock_ms"] for d in got] == [2000]  # "other" kinds are ignored
    publisher.close()
    subscriber.close()


def test_file_subscriber_reads_a_file_created_after_it_started(tmp_path: Path) -> None:
    path = tmp_path / "later.jsonl"
    subscriber = FileSubscriber(path)
    FilePublisher(path).publish([FeedMessage("event", _row(1, 0, event_type="pass"))])
    assert len(list(subscriber.messages(idle_timeout_s=0.2))) == 1


def test_latency_summary() -> None:
    assert latency_summary([])["count"] == 0
    summary = latency_summary([float(v) for v in range(101)])  # 0..100
    assert (summary["p50_ms"], summary["p95_ms"], summary["max_ms"]) == (50, 95, 100)


def test_cli_stream_then_listen_over_the_file_feed(
    match_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PITCH_LEDGER_FEED_FILE", str(tmp_path / "feed.jsonl"))
    assert main(["stream", str(match_dir), "--feed", "file", "--speed", "1000",
                 "--start-min", "10", "--minutes", "1"]) == 0  # fmt: skip
    out = tmp_path / "live"
    assert main(["listen", "--feed", "file", "--from-start", "--idle", "0.3",
                 "--out", str(out)]) == 0  # fmt: skip
    stats = json.loads((out / "listen.json").read_text())
    assert stats["received"]["M0007/tracking"] > 1000
    assert (out / "M0007" / "events.jsonl").exists()
