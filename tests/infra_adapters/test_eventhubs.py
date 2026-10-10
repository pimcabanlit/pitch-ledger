"""Event Hubs feed adapter and feed settings, with fake SDK clients (no Azure calls)."""

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from azure.eventhub import EventData, TransportType

from pitch_ledger.feed import FeedMessage, FilePublisher
from pitch_ledger.infra_adapters.eventhubs import (
    TRACKING_PER_MESSAGE,
    EventHubsPublisher,
    EventHubsSubscriber,
    client_options,
    from_event_data,
    fully_qualified,
    to_event_data,
    to_event_datas,
)
from pitch_ledger.infra_adapters.feeds import FeedSettings, open_publisher


def _msg(match_id: str = "M0002", clock_ms: int = 0, kind: str = "tracking") -> FeedMessage:
    return FeedMessage(kind, {"match_id": match_id, "period": 2, "clock_ms": clock_ms})  # type: ignore[arg-type]


class FakeBatch:
    def __init__(self, partition_key: str, capacity: int) -> None:
        self.partition_key, self.capacity, self.events = partition_key, capacity, []

    def add(self, event: EventData) -> None:
        if len(self.events) == self.capacity:
            raise ValueError("full")
        self.events.append(event)

    def __len__(self) -> int:
        return len(self.events)


class FakeProducer:
    def __init__(self, capacity: int = 2) -> None:
        self.capacity, self.sent, self.closed = capacity, [], False

    def create_batch(self, partition_key: str) -> FakeBatch:
        return FakeBatch(partition_key, self.capacity)

    def send_batch(self, batch: FakeBatch) -> None:
        self.sent.append(batch)

    def close(self) -> None:
        self.closed = True


def test_publish_keeps_bodies_unchanged_and_splits_full_batches() -> None:
    producer = FakeProducer(capacity=2)
    publisher = EventHubsPublisher(producer, clock=lambda: 12.5)
    messages = [_msg(clock_ms=i * 1000, kind="event") for i in range(5)]
    publisher.publish(messages)
    assert [len(b) for b in producer.sent] == [2, 2, 1]
    assert {b.partition_key for b in producer.sent} == {"M0002"}  # order kept per match
    events = [e for b in producer.sent for e in b.events]
    assert [json.loads(e.body_as_str()) for e in events] == [m.row for m in messages]
    assert events[0].properties == {"kind": "event", "sent_at": "12.5"}
    publisher.close()
    assert producer.closed


def test_tracking_runs_are_packed_in_order_and_unpacked_on_receipt() -> None:
    n = TRACKING_PER_MESSAGE
    messages = ([_msg(clock_ms=i) for i in range(n + 3)] + [_msg(clock_ms=500, kind="event")]
                + [_msg(clock_ms=600 + i) for i in range(2)])  # fmt: skip
    events = to_event_datas(messages, 7.0)
    bodies = [json.loads(e.body_as_str()) for e in events]
    assert [len(b) if isinstance(b, list) else "row" for b in bodies] == [n, 3, "row", 2]
    assert [e.properties["kind"] for e in events] == ["tracking", "tracking", "event", "tracking"]
    received = [m for e in events for m in from_event_data(e)]
    assert received == [FeedMessage(m.kind, m.row, 7.0) for m in messages]  # same order


def test_publish_uses_one_partition_key_per_match() -> None:
    producer = FakeProducer(capacity=10)
    EventHubsPublisher(producer).publish(
        [_msg(m, kind="event") for m in ("M0001", "M0002", "M0001")]
    )
    assert [(b.partition_key, len(b)) for b in producer.sent] == [("M0001", 2), ("M0002", 1)]


def test_received_events_convert_back_and_foreign_events_are_skipped() -> None:
    messages = from_event_data(to_event_data(_msg(kind="event"), 3.25))
    assert messages == [FeedMessage("event", _msg().row, 3.25)]
    foreign = EventData("{}")
    foreign.properties = {b"kind": b"telemetry"}  # bytes keys, as some transports deliver
    assert from_event_data(foreign) == []
    for body in ("not json", "[1, 2]"):
        broken = EventData(body)
        broken.properties = {"kind": "tracking"}
        assert from_event_data(broken) == []


class FakeConsumer:
    def __init__(self, events: list[EventData]) -> None:
        self.events, self.kwargs, self.closed = events, {}, False

    def receive(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        kwargs["on_partition_initialize"](None)
        for event in self.events:
            kwargs["on_event"](None, event)
        kwargs["on_error"](None, RuntimeError("link detached\ndetails"))

    def close(self) -> None:
        self.closed = True


def test_subscriber_yields_feed_messages_and_records_errors() -> None:
    packed = to_event_datas([_msg(clock_ms=2), _msg(clock_ms=3)], 1.0)
    consumer = FakeConsumer([to_event_data(_msg(clock_ms=1), 1.0), EventData("{}"), *packed])
    subscriber = EventHubsSubscriber(consumer, clock=lambda: 1.2)
    got = list(subscriber.messages(idle_timeout_s=0.5))
    assert [d.message.row["clock_ms"] for d in got] == [1, 2, 3]
    assert got[0].latency_ms == pytest.approx(200)
    assert subscriber.ready.is_set()
    assert subscriber.last_error == "RuntimeError: link detached"
    assert consumer.kwargs["starting_position"] == "@latest"
    subscriber.close()
    assert consumer.closed


def test_transport_is_plain_amqp_unless_a_proxy_or_websockets_is_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    assert client_options()["transport_type"] == TransportType.Amqp  # Azure, laptop
    assert client_options("websockets")["transport_type"] == TransportType.AmqpOverWebsocket
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    behind_proxy = client_options()
    assert behind_proxy["transport_type"] == TransportType.AmqpOverWebsocket
    assert behind_proxy["http_proxy"] == {"proxy_hostname": "127.0.0.1", "proxy_port": 3128}
    assert client_options("amqp")["transport_type"] == TransportType.Amqp


def test_websocket_transport_dependency_is_installed() -> None:
    """The sync SDK needs websocket-client for AMQP over WebSockets."""
    import websocket  # noqa: F401  (provided by websocket-client)


def test_subscriber_close_gives_up_on_a_hanging_sdk_close() -> None:
    class HangingConsumer(FakeConsumer):
        def close(self) -> None:
            threading.Event().wait(60)  # a stalled link

    subscriber = EventHubsSubscriber(HangingConsumer([]))
    started = time.monotonic()
    subscriber.close(timeout_s=0.2)
    assert time.monotonic() - started < 3


NAMESPACE_FORMS = [
    "pitchledger-feed",
    "pitchledger-feed.servicebus.windows.net",
    "sb://pitchledger-feed.servicebus.windows.net/",
]


@pytest.mark.parametrize("given", NAMESPACE_FORMS)
def test_fully_qualified_namespace(given: str) -> None:
    assert fully_qualified(given) == "pitchledger-feed.servicebus.windows.net"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FEED", "FEED_FILE", "EVENTHUB_NAMESPACE", "EVENTHUB_NAME",
                 "EVENTHUB_CONNECTION", "EVENTHUB_CONSUMER_GROUP"):  # fmt: skip
        monkeypatch.delenv(f"PITCH_LEDGER_{name}", raising=False)


@pytest.mark.usefixtures("clean_env")
def test_settings_name_what_is_missing_and_never_show_the_key() -> None:
    entra = FeedSettings(_env_file=None, feed="eventhubs", eventhub_name="match-feed")  # type: ignore[call-arg]
    assert entra.problems() == [
        "eventhubs feed needs PITCH_LEDGER_EVENTHUB_NAMESPACE (or a connection string)"
    ]
    with pytest.raises(ValueError, match="EVENTHUB_NAMESPACE"):
        open_publisher(entra)
    sas = FeedSettings(  # type: ignore[call-arg]
        _env_file=None,
        feed="eventhubs",
        eventhub_connection="Endpoint=sb://es-123.servicebus.windows.net/;"
        "SharedAccessKeyName=key_1;SharedAccessKey=TOPSECRET=;EntityPath=es_abc",
    )
    assert sas.problems() == []
    assert sas.describe() == "eventhubs es-123.servicebus.windows.net/es_abc (connection string)"
    assert "TOPSECRET" not in repr(sas)


@pytest.mark.usefixtures("clean_env")
def test_file_feed_is_the_default(tmp_path: Path) -> None:
    settings = FeedSettings(_env_file=None, feed_file=tmp_path / "f.jsonl")  # type: ignore[call-arg]
    publisher = open_publisher(settings)
    assert isinstance(publisher, FilePublisher)
    publisher.close()
