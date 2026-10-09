"""Azure Data Explorer event-store adapter (free cluster today; a Fabric Eventhouse speaks
the same protocol, so the same adapter serves it later).

- Writes: rows are queued per table and sent as JSON lines with the table's JSON mapping,
  through managed streaming ingestion (streaming, falling back to queued ingestion for
  large or refused batches). A flush happens at ``max_rows`` queued rows, after
  ``max_age_s`` seconds, or on ``flush()`` / ``close()``.
- Sign-in: Microsoft Entra ID (``DefaultAzureCredential``). The app needs the database
  Ingestor and Viewer roles; tables are created by a database admin with the script from
  ``pitch-ledger store-schema``.
"""

import io
import json
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from pitch_ledger.core.checks import Check, short_error
from pitch_ledger.eventstore.tables import TABLES, mapping_name, project

MAX_REQUEST_BYTES = 3_500_000  # stay under the 4 MB streaming limit


def encode(table: str, rows: Sequence[dict[str, Any]]) -> list[bytes]:
    """JSON lines for a table, split into request-sized chunks."""
    chunks: list[bytes] = []
    current: list[bytes] = []
    size = 0
    for row in rows:
        line = (json.dumps(project(table, row), ensure_ascii=False, default=str) + "\n").encode()
        if current and size + len(line) > MAX_REQUEST_BYTES:
            chunks.append(b"".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line)
    if current:
        chunks.append(b"".join(current))
    return chunks


class AdxEventStore:
    def __init__(
        self,
        database: str,
        ingest: Callable[[str, bytes], None],
        max_rows: int = 5000,
        max_age_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        """``ingest(table, json_lines)`` sends one request (tests pass a fake)."""
        self.database = database
        self._ingest = ingest
        self._max_rows, self._max_age_s, self._clock = max_rows, max_age_s, clock
        self._queued: dict[str, list[dict[str, Any]]] = {}
        self._since = clock()
        self._on_close = on_close
        self.rows_sent: dict[str, int] = {}

    @classmethod
    def connect(cls, cluster_uri: str, database: str) -> "AdxEventStore":
        from azure.identity import DefaultAzureCredential
        from azure.kusto.data import KustoConnectionStringBuilder
        from azure.kusto.data.data_format import DataFormat, IngestionMappingKind
        from azure.kusto.ingest import (
            IngestionProperties,
            ManagedStreamingIngestClient,
            StreamDescriptor,
        )

        credential = DefaultAzureCredential()
        kcsb = KustoConnectionStringBuilder.with_azure_token_credential(cluster_uri, credential)
        client = ManagedStreamingIngestClient(kcsb)

        def ingest(table: str, payload: bytes) -> None:
            props = IngestionProperties(
                database=database,
                table=table,
                data_format=DataFormat.JSON,
                ingestion_mapping_kind=IngestionMappingKind.JSON,
                ingestion_mapping_reference=mapping_name(table),
            )
            client.ingest_from_stream(StreamDescriptor(io.BytesIO(payload)), props)

        def close() -> None:
            client.close()
            credential.close()

        return cls(database, ingest, on_close=close)

    def append(self, table: str, rows: Sequence[dict[str, Any]]) -> None:
        if table not in TABLES:
            raise KeyError(f"unknown event-store table {table!r}")
        self._queued.setdefault(table, []).extend(rows)
        queued = sum(len(r) for r in self._queued.values())
        if queued >= self._max_rows or self._clock() - self._since >= self._max_age_s:
            self.flush()

    def flush(self) -> None:
        for table, rows in self._queued.items():
            for payload in encode(table, rows):
                self._ingest(table, payload)
            self.rows_sent[table] = self.rows_sent.get(table, 0) + len(rows)
        self._queued.clear()
        self._since = self._clock()

    def close(self) -> None:
        self.flush()
        if self._on_close:
            self._on_close()


def check_store(cluster_uri: str | None, database: str | None, wait_s: float = 60.0) -> list[Check]:
    """Settings, Entra ID sign-in, the app's roles, the tables, and one test write read back
    (a row in ``StoreCheck``)."""
    checks = [
        Check("ADX_CLUSTER_URI", bool(cluster_uri), cluster_uri or "not set"),
        Check("ADX_DATABASE", bool(database), database or "not set"),
    ]
    if not (cluster_uri and database):
        return checks
    from azure.identity import DefaultAzureCredential
    from azure.kusto.data import KustoClient, KustoConnectionStringBuilder

    credential = DefaultAzureCredential()
    kcsb = KustoConnectionStringBuilder.with_azure_token_credential(cluster_uri, credential)
    client = KustoClient(kcsb)

    def rows(kind: str, csl: str) -> list[dict[str, Any]]:
        run = client.execute_mgmt if kind == "mgmt" else client.execute_query
        table = run(database, csl).primary_results[0]
        names = [c.column_name for c in table.columns]
        return [dict(zip(names, r, strict=True)) for r in table]

    try:
        rows("query", "print ok=1")
        checks.append(Check("Entra ID sign-in and query", True, "print ok=1"))
        roles = sorted({str(r["Role"]) for r in rows("mgmt", ".show principal roles")})
        checks.append(Check("roles", {"Ingestor", "Viewer"} <= set(roles), ", ".join(roles)))
        found = {str(r["TableName"]) for r in rows("mgmt", ".show tables")}
        missing = [t for t in TABLES if t not in found]
        checks.append(
            Check(
                "tables",
                not missing,
                "all present"
                if not missing
                else f"missing {', '.join(missing)}: run the "
                "script from `pitch-ledger store-schema` in the query editor",
            )
        )
        if missing:
            return checks
        check_id = uuid.uuid4().hex[:12]
        store = AdxEventStore.connect(cluster_uri, database)
        started = time.monotonic()
        store.append("StoreCheck", [{"check_id": check_id, "written_at": datetime.now(UTC)}])
        store.close()
        while True:
            found_rows = rows("query", f"StoreCheck | where check_id == '{check_id}' | count")
            if found_rows and int(found_rows[0]["Count"]) > 0:
                took = time.monotonic() - started
                checks.append(Check("test write", True, f"row readable after {took:.1f} s"))
                break
            if time.monotonic() - started > wait_s:
                checks.append(Check("test write", False, f"not readable after {wait_s:.0f} s"))
                break
            time.sleep(2)
    except Exception as exc:  # sign-in, permission or network failure
        checks.append(Check("ADX", False, short_error(exc)))
    finally:
        client.close()  # type: ignore[no-untyped-call]  # SDK method lacks annotations
        credential.close()
    return checks
