"""Event Hubs feed adapter and feed settings, with fake SDK clients (no Azure calls)."""

import json
from pathlib import Path
from typing import Any

import pytest
from azure.eventhub import EventData

from pitch_ledger.feed import FeedMessage, FilePublisher
from pitch_ledger.infra_adapters.eventhubs import (
    EventHubsPublisher,
    EventHubsSubscriber,
    from_event_data,
    fully_qualified,
    to_event_data,
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
    messages = [_msg(clock_ms=i * 1000) for i in range(5)]
    publisher.publish(messages)
    assert [len(b) for b in producer.sent] == [2, 2, 1]
    assert {b.partition_key for b in producer.sent} == {"M0002"}  # order kept per match
    events = [e for b in producer.sent for e in b.events]
    assert [json.loads(e.body_as_str()) for e in events] == [m.row for m in messages]
    assert events[0].properties == {"kind": "tracking", "sent_at": "12.5"}
    publisher.close()
    assert producer.closed


def test_publish_uses_one_partition_key_per_match() -> None:
    producer = FakeProducer(capacity=10)
    EventHubsPublisher(producer).publish([_msg("M0001"), _msg("M0002"), _msg("M0001")])
    assert [(b.partition_key, len(b)) for b in producer.sent] == [("M0001", 2), ("M0002", 1)]


def test_received_events_convert_back_and_foreign_events_are_skipped() -> None:
    message = from_event_data(to_event_data(_msg(kind="event"), 3.25))
    assert message == FeedMessage("event", _msg().row, 3.25)
    foreign = EventData("{}")
    foreign.properties = {b"kind": b"telemetry"}  # bytes keys, as some transports deliver
    assert from_event_data(foreign) is None
    broken = EventData("not json")
    broken.properties = {"kind": "event"}
    assert from_event_data(broken) is None


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
    consumer = FakeConsumer([to_event_data(_msg(clock_ms=1), 1.0), EventData("{}"),
                             to_event_data(_msg(clock_ms=2), 1.0)])  # fmt: skip
    subscriber = EventHubsSubscriber(consumer, clock=lambda: 1.2)
    got = list(subscriber.messages(idle_timeout_s=0.5))
    assert [d.message.row["clock_ms"] for d in got] == [1, 2]
    assert got[0].latency_ms == pytest.approx(200)
    assert subscriber.ready.is_set()
    assert subscriber.last_error == "RuntimeError: link detached"
    assert consumer.kwargs["starting_position"] == "@latest"
    subscriber.close()
    assert consumer.closed


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
