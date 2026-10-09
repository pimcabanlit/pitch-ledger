"""The live match feed: the Match Lab publishes event and tracking rows, the engine reads them.

``FeedPublisher`` and ``FeedSubscriber`` are the interfaces. Local adapters live in this
package (in-memory for tests, a JSONL file for two local processes). The Azure Event Hubs
adapter in ``pitch_ledger.infra_adapters.eventhubs`` also serves Fabric Eventstream custom
endpoints, which speak the same protocol, so switching is a settings change.

Each message body is one feed row exactly as in ``events.jsonl`` / ``tracking.jsonl``
(docs/event-schema.md: same schema for live and batch). Transport details travel beside
the body: the row kind, and the wall-clock send time used to measure feed latency. A match starts
with one ``match`` message carrying the reference rows (``match.json``: match, teams,
players), so a consumer needs no shared files.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, get_args

FeedKind = Literal["event", "tracking", "match"]
FEED_KINDS: tuple[str, ...] = get_args(FeedKind)


@dataclass(frozen=True)
class FeedMessage:
    """One feed row; ``sent_at`` (Unix seconds) is stamped by the publisher."""

    kind: FeedKind
    row: dict[str, Any]
    sent_at: float | None = None

    @property
    def match_id(self) -> str:
        if self.kind == "match":  # reference message: the match.json content
            return str(self.row["match"]["match_id"])
        return str(self.row["match_id"])


@dataclass(frozen=True)
class Delivered:
    """A message as the subscriber received it."""

    message: FeedMessage
    received_at: float

    @property
    def latency_ms(self) -> float | None:
        sent = self.message.sent_at
        return None if sent is None else (self.received_at - sent) * 1000


class FeedPublisher(Protocol):
    def publish(self, messages: Sequence[FeedMessage]) -> None:
        """Send messages in order; adapters stamp ``sent_at``. One match per call."""
        ...

    def close(self) -> None: ...


class FeedSubscriber(Protocol):
    def messages(self, idle_timeout_s: float) -> Iterator[Delivered]:
        """Yield messages as they arrive; stop after ``idle_timeout_s`` with none."""
        ...

    def close(self) -> None: ...
