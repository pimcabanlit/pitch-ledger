"""Azure Cosmos DB store for match records (container ``history``, partition key ``/season``).

A separate container from the ledger, in the same database and its shared free throughput,
so a replay that empties a match's ledger partition never touches the season's records. One
document per match: the record's fields plus ``id`` (the match ID). Reading a season is a
single-partition query. Sign-in as for the ledger (Microsoft Entra ID).
"""

from typing import Any

from pitch_ledger.infra_adapters.cosmos import Container
from pitch_ledger.schema.history import MatchRecord

DOC_FIELDS = {"id", "_rid", "_self", "_etag", "_attachments", "_ts"}


class CosmosHistory:
    def __init__(self, container: Container, on_close: Any = None) -> None:
        self._container = container
        self._on_close = on_close

    @classmethod
    def connect(
        cls, endpoint: str, database: str, container: str, key: str | None = None
    ) -> "CosmosHistory":
        from azure.cosmos import CosmosClient

        credential: Any = key
        if credential is None:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        client = CosmosClient(endpoint, credential=credential)
        proxy = client.get_database_client(database).get_container_client(container)

        def close() -> None:
            client.close()
            if key is None:
                credential.close()

        return cls(proxy, close)

    def put(self, record: MatchRecord) -> None:
        self._container.upsert_item({**record.model_dump(mode="json"), "id": record.match_id})

    def season(self, competition: str, season: str) -> list[MatchRecord]:
        found = self._container.query_items(
            "SELECT * FROM c WHERE c.competition = @c",
            parameters=[{"name": "@c", "value": competition}],
            partition_key=season,
        )
        records = [
            MatchRecord.model_validate({k: v for k, v in doc.items() if k not in DOC_FIELDS})
            for doc in found
        ]
        return sorted(records, key=lambda r: (r.kickoff_utc, r.match_id))

    def close(self) -> None:
        if self._on_close:
            self._on_close()
