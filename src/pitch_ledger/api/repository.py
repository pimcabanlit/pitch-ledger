"""Read access to matches and their ledgers for the API (local files or Cosmos DB).

Each request gets a fresh ledger view, so the API sees what replays and decisions wrote
since. Match IDs are validated by the routes (``M`` + 4 digits) before they reach a path.
"""

from pathlib import Path
from typing import Any

from pitch_ledger.infra_adapters.ledger_queue import LedgerQueueSettings
from pitch_ledger.ledger import FileLedger, InMemoryLedger
from pitch_ledger.matchlab.io import MATCH_FILE, MatchFiles, read_match


class MatchRepository:
    def __init__(self, data_dir: Path, ledgers: LedgerQueueSettings, container: Any = None):
        """``container`` overrides the Cosmos container (tests pass a fake)."""
        self.data_dir = data_dir
        self.settings = ledgers
        self._container = container

    def match_ids(self) -> list[str]:
        if not self.data_dir.is_dir():
            return []
        return sorted(p.name for p in self.data_dir.iterdir() if (p / MATCH_FILE).is_file())

    def match_dir(self, match_id: str) -> Path | None:
        path = self.data_dir / match_id
        return path if (path / MATCH_FILE).is_file() else None

    def files(self, match_id: str, with_events: bool = True) -> MatchFiles | None:
        path = self.match_dir(match_id)
        if path is None:
            return None
        files = read_match(path, with_tracking=False)
        return files if with_events else MatchFiles(files.match, files.teams, files.players, [], [])

    def _cosmos(self) -> Any:
        if self._container is None:
            from azure.cosmos import CosmosClient

            key: Any = self.settings.key()
            if key is None:
                from azure.identity import DefaultAzureCredential

                key = DefaultAzureCredential()
            client = CosmosClient(self.settings.cosmos_endpoint or "", credential=key)
            database = client.get_database_client(self.settings.cosmos_database)
            self._container = database.get_container_client(self.settings.cosmos_container)
        return self._container

    def ledger(self, match_id: str) -> InMemoryLedger:
        """A fresh view of the match's ledger (empty if none was written)."""
        if self.settings.ledger == "cosmos":
            from pitch_ledger.infra_adapters.cosmos import CosmosLedger

            ledger = CosmosLedger(self._cosmos())
            ledger.load(match_id)
            return ledger
        return FileLedger(self.data_dir / match_id / "ledger")
