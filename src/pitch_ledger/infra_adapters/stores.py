"""Pick the event-store adapter from settings: ``PITCH_LEDGER_STORE=file`` (default,
local JSONL under ``data/store``) or ``adx`` (Azure Data Explorer, located by
``ADX_CLUSTER_URI`` and ``ADX_DATABASE``)."""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pitch_ledger.eventstore import EventStore, FileEventStore


class StoreSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PITCH_LEDGER_", env_file=".env", extra="ignore")

    store: Literal["file", "adx"] = "file"
    store_dir: Path = Path("data/store")
    adx_cluster_uri: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PITCH_LEDGER_ADX_CLUSTER_URI", "ADX_CLUSTER_URI"),
    )
    adx_database: str | None = Field(
        default=None, validation_alias=AliasChoices("PITCH_LEDGER_ADX_DATABASE", "ADX_DATABASE")
    )

    def describe(self) -> str:
        if self.store == "file":
            return f"file {self.store_dir}"
        return f"adx {self.adx_cluster_uri}/{self.adx_database}"


def open_store(settings: StoreSettings, fail_open: bool = False) -> EventStore:
    """``fail_open``: a failed ADX write is logged and skipped (live paths), not raised."""
    if settings.store == "file":
        return FileEventStore(settings.store_dir)
    if not (settings.adx_cluster_uri and settings.adx_database):
        raise ValueError("adx store needs ADX_CLUSTER_URI and ADX_DATABASE")
    from pitch_ledger.infra_adapters.adx import AdxEventStore

    return AdxEventStore.connect(settings.adx_cluster_uri, settings.adx_database, fail_open)
