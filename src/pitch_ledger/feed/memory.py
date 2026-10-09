"""In-memory feed (tests, and single-process runs): one object is publisher and subscriber."""

import queue
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace

from pitch_ledger.feed.port import Delivered, FeedMessage


class InMemoryFeed:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._queue: queue.Queue[FeedMessage] = queue.Queue()

    def publish(self, messages: Sequence[FeedMessage]) -> None:
        now = self._clock()
        for message in messages:
            self._queue.put(replace(message, sent_at=now))

    def messages(self, idle_timeout_s: float) -> Iterator[Delivered]:
        while True:
            try:
                message = self._queue.get(timeout=idle_timeout_s)
            except queue.Empty:
                return
            yield Delivered(message, self._clock())

    def close(self) -> None:
        pass
