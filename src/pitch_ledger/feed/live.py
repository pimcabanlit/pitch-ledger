"""Live replay over the feed: publish a match at match pace, and land what a subscriber reads.

``stream_match`` sends each second of match time as one batch (about 23 rows: 22 tracking
samples plus that second's events), waiting between batches so the feed runs at
``speed`` x real time. Breaks between periods are skipped. ``listen`` writes received rows
to ``<out>/<match_id>/events.jsonl`` and ``tracking.jsonl`` and reports feed latency
(send to receive, one machine's clock when both run together).
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from pitch_ledger.feed.port import Delivered, FeedKind, FeedMessage, FeedPublisher, FeedSubscriber
from pitch_ledger.matchlab.io import EVENTS_JSONL, TRACKING_JSONL, read_match

PERIOD_MS = 45 * 60_000
FILES: dict[FeedKind, str] = {"event": EVENTS_JSONL, "tracking": TRACKING_JSONL}


def match_minute(row: dict[str, Any]) -> float:
    """Period 2 clock 15:00 is minute 60 (as the engine counts it)."""
    return (45.0 if row["period"] == 2 else 0.0) + int(row["clock_ms"]) / 60_000


def feed_seconds(
    events: list[dict[str, Any]], tracking: list[dict[str, Any]]
) -> list[tuple[int, list[FeedMessage]]]:
    """Rows grouped by second of feed time, in feed order (events before tracking within a
    millisecond). Feed time runs on through stoppage and skips the break between periods."""
    rows: list[tuple[FeedKind, dict[str, Any]]] = [("event", r) for r in events]
    rows += [("tracking", r) for r in tracking]
    period_end: dict[int, int] = {}
    for _, row in rows:
        period_end[row["period"]] = max(period_end.get(row["period"], 0), row["clock_ms"])
    offset, start = 0, {}
    for period in sorted(period_end):
        start[period] = offset
        offset += period_end[period] + 1000

    def feed_ms(item: tuple[FeedKind, dict[str, Any]]) -> tuple[int, int]:
        kind, row = item
        return start[row["period"]] + row["clock_ms"], 0 if kind == "event" else 1

    groups: dict[int, list[FeedMessage]] = {}
    for item in sorted(rows, key=feed_ms):  # stable: file order within a millisecond
        groups.setdefault(feed_ms(item)[0] // 1000, []).append(FeedMessage(*item))
    return sorted(groups.items())


@dataclass
class StreamReport:
    match_id: str
    events: int = 0
    tracking: int = 0
    batches: int = 0
    wall_s: float = 0.0
    max_behind_s: float = 0.0


def stream_match(
    match_dir: Path,
    publisher: FeedPublisher,
    speed: float = 1.0,
    start_min: float = 0.0,
    minutes: float | None = None,
    with_tracking: bool = True,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> StreamReport:
    """Publish a match directory's rows at ``speed`` x match pace."""
    if speed <= 0:
        raise ValueError("speed must be positive")
    files = read_match(match_dir, with_tracking=with_tracking)
    end_min = None if minutes is None else start_min + minutes

    def keep(row: dict[str, Any]) -> bool:
        minute = match_minute(row)
        return minute >= start_min and (end_min is None or minute < end_min)

    seconds = feed_seconds(
        [r for r in files.events if keep(r)], [r for r in files.tracking if keep(r)]
    )
    report = StreamReport(files.match.match_id)
    if not seconds:
        return report
    t0, first = clock(), seconds[0][0]
    for second, batch in seconds:
        due = t0 + (second - first) / speed
        wait = due - clock()
        if wait > 0:
            sleep(wait)
        else:
            report.max_behind_s = max(report.max_behind_s, -wait)
        publisher.publish(batch)
        report.batches += 1
        report.events += sum(m.kind == "event" for m in batch)
        report.tracking += sum(m.kind == "tracking" for m in batch)
    report.wall_s = clock() - t0
    return report


def latency_summary(values_ms: list[float]) -> dict[str, float | int | None]:
    if not values_ms:
        return {"count": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}
    ordered = sorted(values_ms)

    def pick(q: float) -> float:
        return round(ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))])

    return {
        "count": len(ordered),
        "p50_ms": pick(0.50),
        "p95_ms": pick(0.95),
        "p99_ms": pick(0.99),
        "max_ms": round(ordered[-1]),
    }


@dataclass
class ListenReport:
    out_dir: Path
    received: dict[str, int] = field(default_factory=dict)
    latency_ms: dict[FeedKind, list[float]] = field(
        default_factory=lambda: {"event": [], "tracking": []}
    )
    full_time: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "out_dir": str(self.out_dir),
            "received": self.received,
            "full_time": self.full_time,
            "latency": {k: latency_summary(v) for k, v in self.latency_ms.items()},
        }


def _is_full_time(message: FeedMessage) -> bool:
    row = message.row
    return message.kind == "event" and row["event_type"] == "period_end" and row["period"] == 2


def listen(
    subscriber: FeedSubscriber,
    out_dir: Path,
    idle_timeout_s: float = 30.0,
    stop_at_full_time: bool = True,
    on_message: Callable[[Delivered], None] | None = None,
) -> ListenReport:
    """Land every received row under ``out_dir``; stop at full time or after an idle gap.
    Files for a match are started fresh the first time this run sees it."""
    report = ListenReport(out_dir)
    handles: dict[tuple[str, FeedKind], IO[str]] = {}
    try:
        for delivered in subscriber.messages(idle_timeout_s):
            m = delivered.message
            key = (m.match_id, m.kind)
            if key not in handles:
                (out_dir / m.match_id).mkdir(parents=True, exist_ok=True)
                path = out_dir / m.match_id / FILES[m.kind]
                handles[key] = path.open("w", encoding="utf-8", newline="\n")
            line = json.dumps(m.row, ensure_ascii=False, separators=(",", ":"))
            handles[key].write(line + "\n")
            label = f"{m.match_id}/{m.kind}"
            report.received[label] = report.received.get(label, 0) + 1
            if delivered.latency_ms is not None:
                report.latency_ms[m.kind].append(delivered.latency_ms)
            if on_message:
                on_message(delivered)
            if stop_at_full_time and _is_full_time(m):
                report.full_time = True
                break
    finally:
        for fh in handles.values():
            fh.close()
    return report
