"""Azure Cosmos DB store for match records, in the ledger's container.

The ledger container is partitioned by ``/match_id``. A season's records share one
partition of their own, ``history:<season>`` (one document per match: ``id``
``record-<match>``, ``kind`` ``match_record``, the record under ``record``). Match IDs look
like M0002, so no match's ledger, and no replay emptying a match's partition, ever touches
them. No container is added, so the account's free throughput limit is unchanged. Reading a
season is a single-partition query. Sign-in as for the ledger (Microsoft Entra ID).
"""

from typing import Any

from pitch_ledger.infra_adapters.cosmos import Container
from pitch_ledger.schema.history import MatchRecord

KIND = "match_record"


def partition(season: str) -> str:
    return f"history:{season}"


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
        self._container.upsert_item({
            "id": f"record-{record.match_id}", "kind": KIND,
            "match_id": partition(record.season), "competition": record.competition,
            "season": record.season, "record": record.model_dump(mode="json"),
        })  # fmt: skip

    def season(self, competition: str, season: str) -> list[MatchRecord]:
        found = self._container.query_items(
            "SELECT * FROM c WHERE c.kind = @k AND c.competition = @c",
            parameters=[{"name": "@k", "value": KIND}, {"name": "@c", "value": competition}],
            partition_key=partition(season),
        )
        records = [MatchRecord.model_validate(doc["record"]) for doc in found]
        return sorted(records, key=lambda r: (r.kickoff_utc, r.match_id))

    def close(self) -> None:
        if self._on_close:
            self._on_close()
