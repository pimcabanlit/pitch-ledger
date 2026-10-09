"""Azure Event Hubs feed adapter. Also serves Fabric Eventstream custom endpoints, which
expose the same Event Hubs protocol: point the settings at the endpoint, no code change.

- Body: the feed row as compact JSON, unchanged. Properties: ``kind`` and ``sent_at``.
- Partition key: the match ID, so a match's rows stay in order (Event Hubs orders within
  a partition).
- Transport (``auto``): plain AMQP (port 5671) when there is no proxy, as in Azure; AMQP over
  WebSockets (port 443) through ``HTTPS_PROXY`` when one is set. ``websockets`` forces 443.
- Sign-in: Microsoft Entra ID (``DefaultAzureCredential``: service principal variables
  locally, managed identity in Azure) with namespace + hub name, or a SAS connection
  string (what Eventstream custom endpoints hand out).
"""

import json
import logging
import os
import queue
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from typing import Any, Literal, cast
from urllib.parse import urlparse

from azure.eventhub import (
    EventData,
    EventHubConsumerClient,
    EventHubProducerClient,
    PartitionContext,
    TransportType,
)

from pitch_ledger.feed.port import FEED_KINDS, Delivered, FeedKind, FeedMessage

log = logging.getLogger(__name__)


Transport = Literal["auto", "amqp", "websockets"]


def client_options(transport: Transport = "auto") -> dict[str, Any]:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if transport == "amqp" or (transport == "auto" and not proxy):
        options: dict[str, Any] = {"transport_type": TransportType.Amqp}
    else:
        options = {"transport_type": TransportType.AmqpOverWebsocket}
        if proxy:
            url = urlparse(proxy)
            options["http_proxy"] = {"proxy_hostname": url.hostname, "proxy_port": url.port or 80}
    ca_bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca_bundle:
        options["connection_verify"] = ca_bundle
    return options


def fully_qualified(namespace: str) -> str:
    """``pitchledger-feed`` or ``sb://pitchledger-feed.servicebus.windows.net/`` ->
    ``pitchledger-feed.servicebus.windows.net``."""
    host = urlparse(namespace).hostname if "//" in namespace else namespace.strip("/")
    host = host or namespace
    return host if "." in host else f"{host}.servicebus.windows.net"


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def to_event_data(message: FeedMessage, sent_at: float) -> EventData:
    event = EventData(json.dumps(message.row, ensure_ascii=False, separators=(",", ":")))
    event.properties = {"kind": message.kind, "sent_at": repr(sent_at)}
    return event


def from_event_data(event: EventData) -> FeedMessage | None:
    """The feed message in a received event; None for anything else on the hub."""
    props = {_text(k): _text(v) for k, v in (event.properties or {}).items()}
    kind = props.get("kind")
    if kind not in FEED_KINDS:
        return None
    try:
        row = json.loads(event.body_as_str())
        sent_at = float(props["sent_at"]) if "sent_at" in props else None
    except (ValueError, TypeError):
        return None
    return FeedMessage(cast(FeedKind, kind), row, sent_at)


class EventHubsPublisher:
    def __init__(
        self, producer: Any, credential: Any = None, clock: Callable[[], float] = time.time
    ) -> None:
        """``producer`` is an ``EventHubProducerClient`` (tests pass a fake)."""
        self._producer = producer
        self._credential = credential
        self._clock = clock

    @classmethod
    def connect(
        cls,
        namespace: str | None,
        eventhub: str | None,
        connection: str | None,
        transport: Transport = "auto",
    ) -> "EventHubsPublisher":
        if connection:
            producer = EventHubProducerClient.from_connection_string(
                connection, eventhub_name=eventhub, **client_options(transport)
            )
            return cls(producer)
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        producer = EventHubProducerClient(
            fully_qualified(namespace or ""),
            eventhub or "",
            credential,
            **client_options(transport),
        )
        return cls(producer, credential)

    def publish(self, messages: Sequence[FeedMessage]) -> None:
        by_match: dict[str, list[FeedMessage]] = {}
        for message in messages:
            by_match.setdefault(message.match_id, []).append(message)
        now = self._clock()
        for match_id, group in by_match.items():
            batch = self._producer.create_batch(partition_key=match_id)
            for message in group:
                event = to_event_data(message, now)
                try:
                    batch.add(event)
                except ValueError:  # batch full: send it and start the next one
                    self._producer.send_batch(batch)
                    batch = self._producer.create_batch(partition_key=match_id)
                    batch.add(event)
            self._producer.send_batch(batch)

    def close(self) -> None:
        self._producer.close()
        if self._credential is not None:
            self._credential.close()


class EventHubsSubscriber:
    def __init__(
        self,
        consumer: Any,
        credential: Any = None,
        from_start: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """``consumer`` is an ``EventHubConsumerClient`` (tests pass a fake)."""
        self._consumer = consumer
        self._credential = credential
        self._clock = clock
        self._queue: queue.Queue[Delivered] = queue.Queue()
        self.ready = threading.Event()
        self.last_error: str | None = None
        self._thread = threading.Thread(
            target=self._consumer.receive,
            kwargs={
                "on_event": self._on_event,
                "on_error": self._on_error,
                "on_partition_initialize": lambda _ctx: self.ready.set(),
                "starting_position": "-1" if from_start else "@latest",
                "max_wait_time": 1,
            },
            daemon=True,
        )
        self._thread.start()

    @classmethod
    def connect(
        cls,
        namespace: str | None,
        eventhub: str | None,
        connection: str | None,
        consumer_group: str,
        from_start: bool = False,
        transport: Transport = "auto",
    ) -> "EventHubsSubscriber":
        if connection:
            consumer = EventHubConsumerClient.from_connection_string(
                connection, consumer_group, eventhub_name=eventhub, **client_options(transport)
            )
            return cls(consumer, from_start=from_start)
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        consumer = EventHubConsumerClient(
            fully_qualified(namespace or ""),
            eventhub or "",
            consumer_group,
            credential,
            **client_options(transport),
        )
        return cls(consumer, credential, from_start)

    def _on_event(self, _ctx: PartitionContext, event: EventData | None) -> None:
        now = self._clock()
        message = from_event_data(event) if event is not None else None
        if message is not None:
            self._queue.put(Delivered(message, now))

    def _on_error(self, _ctx: PartitionContext | None, error: Exception) -> None:
        self.last_error = f"{type(error).__name__}: {str(error).splitlines()[0][:300]}"
        log.warning("event hubs receive error: %s", self.last_error)

    def messages(self, idle_timeout_s: float) -> Iterator[Delivered]:
        while True:
            try:
                yield self._queue.get(timeout=idle_timeout_s)
            except queue.Empty:
                return

    def close(self, timeout_s: float = 10.0) -> None:
        """Stop receiving. The SDK's close can block for minutes on a stalled link (seen
        behind a proxy), so it runs on a daemon thread and is abandoned after the timeout."""
        closer = threading.Thread(target=self._consumer.close, daemon=True)
        closer.start()
        closer.join(timeout=timeout_s)
        self._thread.join(timeout=1)
        if self._credential is not None:
            self._credential.close()
