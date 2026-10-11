"""Pick the ledger and trigger-queue adapters from settings.

- ``PITCH_LEDGER_LEDGER``: ``file`` (default; JSONL under ``<match>/ledger``) or
  ``cosmos`` (``COSMOS_ENDPOINT``, database ``pitchledger``, container ``ledger``).
- ``PITCH_LEDGER_QUEUE``: ``memory`` (default) or ``servicebus``
  (``PITCH_LEDGER_SERVICEBUS_NAMESPACE``, queue ``triggers``).
- ``PITCH_LEDGER_HISTORY``: where match records are kept, ``file`` (default;
  ``<data>/history/records.jsonl``) or ``cosmos`` (the ledger's container, one partition
  per season).
"""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from pitch_ledger.agents.queue import InMemoryTriggerQueue, TriggerQueue
from pitch_ledger.history.store import HISTORY_DIR, FileHistory, HistoryStore
from pitch_ledger.ledger import InMemoryLedger


class LedgerQueueSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PITCH_LEDGER_", env_file=".env", extra="ignore")

    ledger: Literal["file", "cosmos"] = "file"
    cosmos_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PITCH_LEDGER_COSMOS_ENDPOINT", "COSMOS_ENDPOINT"),
    )
    cosmos_key: SecretStr | None = None  # account key (secret); Entra ID when unset
    cosmos_database: str = "pitchledger"
    cosmos_container: str = "ledger"
    history: Literal["file", "cosmos"] = "file"
    queue: Literal["memory", "servicebus"] = "memory"
    servicebus_namespace: str | None = None  # <name>.servicebus.windows.net
    servicebus_queue: str = "triggers"
    servicebus_connection: SecretStr | None = None  # connection string (secret)
    servicebus_transport: Literal["auto", "amqp", "websockets"] = "auto"

    def key(self) -> str | None:
        return self.cosmos_key.get_secret_value() if self.cosmos_key else None

    def connection(self) -> str | None:
        return self.servicebus_connection.get_secret_value() if self.servicebus_connection else None

    def describe_ledger(self) -> str:
        if self.ledger == "file":
            return "file"
        return f"cosmos {self.cosmos_endpoint} {self.cosmos_database}/{self.cosmos_container}"

    def describe_queue(self) -> str:
        if self.queue == "memory":
            return "memory"
        return (
            f"servicebus {self.servicebus_namespace or 'connection string'}/{self.servicebus_queue}"
        )


def open_ledger(settings: LedgerQueueSettings, fresh_match: str) -> InMemoryLedger | None:
    """The Cosmos ledger, emptied for ``fresh_match`` (a new replay); None for ``file`` (the
    replay makes its own file ledger)."""
    if settings.ledger == "file":
        return None
    if not settings.cosmos_endpoint:
        raise ValueError("cosmos ledger needs COSMOS_ENDPOINT")
    from pitch_ledger.infra_adapters.cosmos import CosmosLedger

    return CosmosLedger.connect(
        settings.cosmos_endpoint,
        settings.cosmos_database,
        settings.cosmos_container,
        settings.key(),
        fresh_matches=(fresh_match,),
    )


def describe_history(settings: LedgerQueueSettings) -> str:
    if settings.history == "file":
        return "file"
    return f"cosmos {settings.cosmos_database}/{settings.cosmos_container} (history:<season>)"


def open_history(settings: LedgerQueueSettings, data_dir: Path) -> HistoryStore:
    """Match records: ``<data_dir>/history/records.jsonl``, or the Cosmos DB container."""
    if settings.history == "file":
        return FileHistory(data_dir / HISTORY_DIR)
    if not settings.cosmos_endpoint:
        raise ValueError("cosmos history needs COSMOS_ENDPOINT")
    from pitch_ledger.infra_adapters.cosmos_history import CosmosHistory

    return CosmosHistory.connect(
        settings.cosmos_endpoint,
        settings.cosmos_database,
        settings.cosmos_container,
        settings.key(),
    )


def open_queue(settings: LedgerQueueSettings) -> TriggerQueue:
    if settings.queue == "memory":
        return InMemoryTriggerQueue()
    if not (settings.servicebus_namespace or settings.servicebus_connection):
        raise ValueError("servicebus queue needs PITCH_LEDGER_SERVICEBUS_NAMESPACE")
    from pitch_ledger.infra_adapters.servicebus import ServiceBusTriggerQueue

    return ServiceBusTriggerQueue.connect(
        settings.servicebus_namespace,
        settings.servicebus_queue,
        settings.connection(),
        settings.servicebus_transport,
    )
