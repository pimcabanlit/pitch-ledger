"""Local file feed: the stand-in for Event Hubs when publisher and subscriber are two local
processes (``pitch-ledger stream`` in one terminal, ``pitch-ledger listen`` in another).

The publisher appends one JSON line per message, ``{"kind", "sent_at", "row"}``; the
subscriber tails the file. Start the subscriber first unless it reads from the start.
"""

import json
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import IO

from pitch_ledger.feed.port import FEED_KINDS, Delivered, FeedMessage

POLL_S = 0.05


class FilePublisher:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._fh = path.open("a", encoding="utf-8", newline="\n")

    def publish(self, messages: Sequence[FeedMessage]) -> None:
        now = self._clock()
        for m in messages:
            line = json.dumps({"kind": m.kind, "sent_at": now, "row": m.row}, ensure_ascii=False)
            self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class FileSubscriber:
    def __init__(
        self, path: Path, from_start: bool = False, clock: Callable[[], float] = time.time
    ) -> None:
        self._path = path
        self._clock = clock
        self._fh: IO[str] | None = None
        self._partial = ""  # a line the writer has not finished yet
        if path.exists() and not from_start:
            self._fh = path.open(encoding="utf-8")
            self._fh.seek(0, 2)  # skip what was published before we started

    def _open(self) -> IO[str] | None:
        if self._fh is None and self._path.exists():  # created after we started: all new
            self._fh = self._path.open(encoding="utf-8")
        return self._fh

    def messages(self, idle_timeout_s: float) -> Iterator[Delivered]:
        last = self._clock()
        while True:
            fh = self._open()
            chunk = fh.readline() if fh else ""
            if not chunk:
                if self._clock() - last >= idle_timeout_s:
                    return
                time.sleep(POLL_S)
                continue
            self._partial += chunk
            if not self._partial.endswith("\n"):
                continue  # the writer is mid-line
            line, self._partial = self._partial.strip(), ""
            last = self._clock()
            if not line:
                continue
            record = json.loads(line)
            if record.get("kind") in FEED_KINDS:
                message = FeedMessage(record["kind"], record["row"], record.get("sent_at"))
                yield Delivered(message, last)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
