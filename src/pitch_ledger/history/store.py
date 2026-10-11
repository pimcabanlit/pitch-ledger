"""Where match records are kept: the port, an in-memory store and a local file store.

A store holds at most one record per match: ``put`` replaces it, which is safe because a
record is deterministic (the same match files give the same record). The Cosmos DB store is
in ``infra_adapters.cosmos_history``.
"""

import json
from pathlib import Path
from typing import Protocol

from pitch_ledger.engine.season import SeasonView
from pitch_ledger.schema import Match
from pitch_ledger.schema.history import MatchRecord

HISTORY_DIR = "history"
RECORDS_FILE = "records.jsonl"


class HistoryStore(Protocol):
    def put(self, record: MatchRecord) -> None: ...

    def season(self, competition: str, season: str) -> list[MatchRecord]:
        """Every record of a competition's season, in kick-off order."""
        ...


def _ordered(records: list[MatchRecord]) -> list[MatchRecord]:
    return sorted(records, key=lambda r: (r.kickoff_utc, r.match_id))


class InMemoryHistory:
    def __init__(self, records: list[MatchRecord] | None = None) -> None:
        self.records: dict[str, MatchRecord] = {r.match_id: r for r in records or []}

    def put(self, record: MatchRecord) -> None:
        self.records[record.match_id] = record

    def season(self, competition: str, season: str) -> list[MatchRecord]:
        return _ordered([r for r in self.records.values()
                         if (r.competition, r.season) == (competition, season)])  # fmt: skip


class FileHistory(InMemoryHistory):
    """Records as JSON lines in ``<data>/history/records.jsonl`` (rewritten on each put)."""

    def __init__(self, root: Path, name: str = RECORDS_FILE) -> None:
        self.path = root / name
        lines = self.path.read_text("utf-8").splitlines() if self.path.exists() else []
        super().__init__([MatchRecord.model_validate_json(x) for x in lines if x.strip()])

    def put(self, record: MatchRecord) -> None:
        super().put(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = _ordered(list(self.records.values()))
        self.path.write_text(
            "".join(json.dumps(r.model_dump(mode="json")) + "\n" for r in rows), "utf-8"
        )


def season_for(match: Match, store: HistoryStore | None) -> SeasonView | None:
    """The match's view of its season (earlier records only); None without a store."""
    if store is None:
        return None
    return SeasonView.before(match, store.season(match.competition, match.season))
