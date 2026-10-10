"""Azure Cosmos DB ledger adapter (NoSQL API, free tier).

One container (``ledger``, partition key ``/match_id``): every trigger, insight, audit
record, overlay and ops alert is a document ``{"id", "match_id", "kind", ...model fields}``,
so a match's ledger is one partition. An ops alert's own ``kind`` (feed_gap, ...) is stored
as ``alert_kind``, since ``kind`` names the document type. The adapter is the in-memory
ledger with write-through: reads come from memory, every write is upserted at once (the
Studio sees it live), and the first time a match is touched its documents are loaded so ID
counters continue. That assumes one writer per match, which is the architecture (one
orchestrator).

Sign-in: Microsoft Entra ID with the *Cosmos DB Built-in Data Contributor* data-plane role
(assigned with the Azure CLI; see docs/azure-setup.md), or an account key.
"""

import time
import uuid
from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from pitch_ledger.core.checks import Check, short_error
from pitch_ledger.ledger import InMemoryLedger
from pitch_ledger.ledger.repository import IdKind
from pitch_ledger.schema import AuditRecord, Insight, OpsAlert, Overlay, Trigger

DocKind = Literal["trigger", "insight", "audit", "overlay", "alert"]
ID_KIND: dict[DocKind, IdKind | None] = {
    "trigger": None,
    "insight": "I",
    "audit": "A",
    "overlay": "O",
    "alert": "X",
}


class Container(Protocol):
    """The slice of ``azure.cosmos.ContainerProxy`` the ledger uses (tests pass a fake)."""

    def upsert_item(self, body: dict[str, Any]) -> Any: ...

    def query_items(self, query: str, **kwargs: Any) -> Any: ...

    def delete_item(self, item: str, partition_key: str) -> Any: ...


def to_doc(kind: DocKind, ident: str, model: BaseModel) -> dict[str, Any]:
    return {**model.model_dump(mode="json"), "id": ident, "kind": kind}


class CosmosLedger(InMemoryLedger):
    def __init__(
        self,
        container: Container,
        fresh_matches: tuple[str, ...] = (),
        on_close: Callable[[], None] | None = None,
        fresh_all: bool = False,
    ) -> None:
        """``fresh_matches`` (or every match, with ``fresh_all``) are emptied the first time
        they are touched (a new replay or live run)."""
        super().__init__()
        self._container = container
        self._fresh = set(fresh_matches)
        self._fresh_all = fresh_all
        self._loaded: set[str] = set()
        self._on_close = on_close
        self.writes = 0

    @classmethod
    def connect(
        cls,
        endpoint: str,
        database: str,
        container: str,
        key: str | None = None,
        fresh_matches: tuple[str, ...] = (),
        fresh_all: bool = False,
    ) -> "CosmosLedger":
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

        return cls(proxy, fresh_matches, close, fresh_all)

    def _docs(self, match_id: str) -> list[dict[str, Any]]:
        return list(
            self._container.query_items(
                "SELECT * FROM c WHERE c.match_id = @m",
                parameters=[{"name": "@m", "value": match_id}],
                partition_key=match_id,
            )
        )

    def _touch(self, match_id: str) -> None:
        if match_id in self._loaded:
            return
        self._loaded.add(match_id)
        docs = self._docs(match_id)
        if match_id in self._fresh or self._fresh_all:
            for doc in docs:
                self._container.delete_item(doc["id"], partition_key=match_id)
            return
        for doc in docs:
            body = {
                k: v for k, v in doc.items() if k not in ("id", "kind") and not k.startswith("_")
            }
            kind: DocKind = doc["kind"]
            if kind == "trigger":
                super().save_trigger(Trigger.model_validate(body))
            elif kind == "insight":
                super().put_insight(Insight.model_validate(body))
            elif kind == "audit":
                super().add_audit(AuditRecord.model_validate(body))
            elif kind == "overlay":  # match_id is the partition key only, not an overlay field
                body.pop("match_id", None)
                super().put_overlay(Overlay.model_validate(body))
            elif kind == "alert":
                body["kind"] = body.pop("alert_kind")
                super().put_alert(OpsAlert.model_validate(body))
            id_kind = ID_KIND[kind]
            if id_kind is not None:
                number = int(doc["id"].split(f"-{id_kind}")[1])
                key = (match_id, id_kind)
                self._counters[key] = max(self._counters[key], number)

    def load(self, match_id: str) -> None:
        """Read a match's documents now (they also load on the first write or new ID)."""
        self._touch(match_id)

    def _write(self, match_id: str, doc: dict[str, Any]) -> None:
        self._container.upsert_item(doc)
        self.writes += 1

    def next_id(self, match_id: str, kind: IdKind) -> str:
        self._touch(match_id)
        return super().next_id(match_id, kind)

    def save_trigger(self, trigger: Trigger) -> None:
        self._touch(trigger.match_id)
        super().save_trigger(trigger)
        self._write(trigger.match_id, to_doc("trigger", trigger.trigger_id, trigger))

    def put_insight(self, insight: Insight) -> None:
        self._touch(insight.match_id)
        super().put_insight(insight)
        self._write(insight.match_id, to_doc("insight", insight.insight_id, insight))

    def add_audit(self, record: AuditRecord) -> None:
        self._touch(record.match_id)
        super().add_audit(record)
        self._write(record.match_id, to_doc("audit", record.audit_id, record))

    def put_overlay(self, overlay: Overlay) -> None:
        match_id = overlay.overlay_id.split("-O")[0]
        self._touch(match_id)
        super().put_overlay(overlay)
        self._write(
            match_id, {**to_doc("overlay", overlay.overlay_id, overlay), "match_id": match_id}
        )

    def put_alert(self, alert: OpsAlert) -> None:
        self._touch(alert.match_id)
        super().put_alert(alert)
        doc = {**to_doc("alert", alert.alert_id, alert), "alert_kind": alert.kind}
        self._write(alert.match_id, doc)

    def close(self) -> None:
        if self._on_close:
            self._on_close()


def check_ledger(
    endpoint: str | None, database: str, container: str, key: str | None
) -> list[Check]:
    """Settings, sign-in and one document written, read back and deleted."""
    checks = [Check("COSMOS_ENDPOINT", bool(endpoint), endpoint or "not set")]
    if not endpoint:
        return checks
    checks.append(Check("sign-in", True, "account key" if key else "Entra ID"))
    try:
        from azure.cosmos import CosmosClient

        credential: Any = key
        if credential is None:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        client = CosmosClient(endpoint, credential=credential)
        proxy = client.get_database_client(database).get_container_client(container)
        doc = {"id": f"check-{uuid.uuid4().hex[:12]}", "match_id": "CHECK", "kind": "check"}
        started = time.monotonic()
        proxy.upsert_item(doc)
        proxy.read_item(doc["id"], partition_key="CHECK")
        proxy.delete_item(doc["id"], partition_key="CHECK")
        took = time.monotonic() - started
        checks.append(
            Check(f"{database}/{container}", True, f"write, read, delete in {took:.1f} s")
        )
        client.close()
    except Exception as exc:  # sign-in, permission, missing container or network
        checks.append(Check(f"{database}/{container}", False, short_error(exc)))
    return checks
