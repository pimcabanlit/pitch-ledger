"""The event store interface and the local-file adapter.

``EventStore`` is the interface; ``FileEventStore`` writes one JSONL file per table under
a folder (local-first, tests), and the Azure Data Explorer adapter in
``pitch_ledger.infra_adapters.adx`` implements the same interface.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any, Protocol

from pitch_ledger.eventstore.tables import TABLES, project


class EventStore(Protocol):
    def append(self, table: str, rows: Sequence[dict[str, Any]]) -> None:
        """Queue rows for a table (extra keys dropped, missing columns null)."""
        ...

    def flush(self) -> None:
        """Write everything queued so far."""
        ...

    def close(self) -> None: ...


class FileEventStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._handles: dict[str, IO[str]] = {}

    def append(self, table: str, rows: Sequence[dict[str, Any]]) -> None:
        if table not in TABLES:
            raise KeyError(f"unknown event-store table {table!r}")
        if table not in self._handles:
            self.root.mkdir(parents=True, exist_ok=True)
            self._handles[table] = (self.root / f"{table}.jsonl").open("a", encoding="utf-8")
        fh = self._handles[table]
        for row in rows:
            fh.write(json.dumps(project(table, row), ensure_ascii=False, default=str) + "\n")

    def flush(self) -> None:
        for fh in self._handles.values():
            fh.flush()

    def close(self) -> None:
        for fh in self._handles.values():
            fh.close()
        self._handles.clear()
