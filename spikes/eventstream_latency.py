"""Latency spike: time the hop from a Fabric Eventstream to a local Python consumer.

Replays a slice of a generated match at live pace (1 s of match per 1 s of wall time:
each second's tracking rows as one batch, plus that second's events) into the
Eventstream's custom-endpoint source, reads them back from its custom-endpoint
destination, and reports send-to-receive latency. Sender and receiver run in the same
process, so both timestamps use one clock.

Throwaway measurement script for the Fabric vs Event Hubs-direct decision
(docs/architecture.md, open items). Not part of the package.

Settings (environment variables; the connection strings are secrets):
  PITCH_LEDGER_ES_SEND_CONNECTION   source custom endpoint, Event Hub protocol, SAS
                                    connection string (must include EntityPath)
  PITCH_LEDGER_ES_RECV_CONNECTION   destination custom endpoint, same format
  PITCH_LEDGER_ES_CONSUMER_GROUP    destination consumer group (default $Default)

Run:
  uv run --group spike python spikes/eventstream_latency.py data/M0002 --seconds 120
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from azure.eventhub import (
    EventData,
    EventHubConsumerClient,
    EventHubProducerClient,
    PartitionContext,
    TransportType,
)


def client_kwargs() -> dict[str, Any]:
    """AMQP over WebSockets (port 443) so it works behind an HTTPS proxy and most firewalls."""
    kwargs: dict[str, Any] = {"transport_type": TransportType.AmqpOverWebsocket}
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        url = urlparse(proxy)
        kwargs["http_proxy"] = {"proxy_hostname": url.hostname, "proxy_port": url.port or 80}
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca:
        kwargs["connection_verify"] = ca
    return kwargs


def load_slice(match_dir: Path, start_min: float, seconds: int) -> list[list[dict[str, Any]]]:
    """Rows grouped by wall-clock second: tracking rows plus events in that match second."""
    t0 = int(start_min * 60_000)
    period = 2 if start_min >= 45 else 1
    p_start = 45 * 60_000 if period == 2 else 0
    lo, hi = t0 - p_start, t0 - p_start + seconds * 1000
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(seconds)]
    for name in ("events.jsonl", "tracking.jsonl"):
        with open(match_dir / name, encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                if row["period"] == period and lo <= row["clock_ms"] < hi:
                    buckets[(row["clock_ms"] - lo) // 1000].append(row)
    return buckets


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def summarize(latencies_ms: list[float], sent: int) -> dict[str, Any]:
    if not latencies_ms:
        return {"sent": sent, "received": 0}
    return {
        "sent": sent,
        "received": len(latencies_ms),
        "lost": sent - len(latencies_ms),
        "p50_ms": round(percentile(latencies_ms, 0.50)),
        "p95_ms": round(percentile(latencies_ms, 0.95)),
        "p99_ms": round(percentile(latencies_ms, 0.99)),
        "max_ms": round(max(latencies_ms)),
        "mean_ms": round(statistics.fmean(latencies_ms)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("match_dir", type=Path)
    ap.add_argument("--start-min", type=float, default=60.0, help="match minute to start at")
    ap.add_argument("--seconds", type=int, default=120, help="seconds of match to replay")
    ap.add_argument("--drain-s", type=float, default=30.0, help="wait for stragglers")
    ap.add_argument("--out", type=Path, default=Path("data/spikes/eventstream_latency.json"))
    args = ap.parse_args()

    send_conn = os.environ["PITCH_LEDGER_ES_SEND_CONNECTION"]
    recv_conn = os.environ["PITCH_LEDGER_ES_RECV_CONNECTION"]
    group = os.environ.get("PITCH_LEDGER_ES_CONSUMER_GROUP", "$Default")
    run_id = uuid.uuid4().hex[:8]
    buckets = load_slice(args.match_dir, args.start_min, args.seconds)

    sent_at: dict[int, float] = {}
    got: dict[int, float] = {}
    kinds: dict[int, str] = {}
    lock = threading.Lock()
    ready = threading.Event()

    def on_event(_ctx: PartitionContext, event: EventData | None) -> None:
        now = time.time()
        if event is None:
            return
        try:
            probe = json.loads(event.body_as_str())["_probe"]
        except (ValueError, KeyError, TypeError):
            return  # someone else's message, or the body was reshaped
        if probe.get("run") == run_id:
            with lock:
                got.setdefault(probe["seq"], now)

    consumer = EventHubConsumerClient.from_connection_string(
        recv_conn, consumer_group=group, **client_kwargs()
    )
    receiver = threading.Thread(
        target=consumer.receive,
        kwargs={
            "on_event": on_event,
            "starting_position": "@latest",
            "on_partition_initialize": lambda _ctx: ready.set(),
            "max_wait_time": 1,
        },
        daemon=True,
    )
    receiver.start()
    if not ready.wait(60):
        raise SystemExit("receiver did not connect within 60 s (check host allow-list / string)")
    time.sleep(2)  # let every partition attach before sending

    producer = EventHubProducerClient.from_connection_string(send_conn, **client_kwargs())
    seq = 0
    t_start = time.time()
    with producer:
        for i, rows in enumerate(buckets):
            batch = producer.create_batch()
            for row in rows:
                row = dict(row, _probe={"run": run_id, "seq": seq})
                kinds[seq] = "event" if "event_id" in row else "tracking"
                sent_at[seq] = time.time()
                batch.add(EventData(json.dumps(row)))
                seq += 1
            if len(batch):
                producer.send_batch(batch)
            time.sleep(max(0.0, t_start + i + 1 - time.time()))

    deadline = time.time() + args.drain_s
    while time.time() < deadline and len(got) < seq:
        time.sleep(0.5)
    consumer.close()

    lat = {s: (got[s] - sent_at[s]) * 1000 for s in got}
    result = {
        "run_id": run_id,
        "match_dir": str(args.match_dir),
        "start_min": args.start_min,
        "seconds": args.seconds,
        "all": summarize(list(lat.values()), seq),
        "events": summarize(
            [v for s, v in lat.items() if kinds[s] == "event"],
            sum(1 for k in kinds.values() if k == "event"),
        ),
        "first_message_ms": round(lat[min(lat)]) if lat else None,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
