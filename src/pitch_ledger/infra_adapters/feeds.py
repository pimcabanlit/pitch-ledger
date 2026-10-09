"""Pick the live-feed adapter from settings: ``PITCH_LEDGER_FEED=file`` (default, local)
or ``eventhubs`` (Azure Event Hubs, or a Fabric Eventstream custom endpoint)."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from pitch_ledger.feed import FeedPublisher, FeedSubscriber, FilePublisher, FileSubscriber


class FeedSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PITCH_LEDGER_", env_file=".env", extra="ignore")

    feed: Literal["file", "eventhubs"] = "file"
    feed_file: Path = Path("data/feed/feed.jsonl")
    eventhub_namespace: str | None = None  # <name>.servicebus.windows.net
    eventhub_name: str | None = None
    eventhub_connection: SecretStr | None = None  # SAS connection string (secret)
    eventhub_consumer_group: str = "$Default"

    def connection(self) -> str | None:
        return self.eventhub_connection.get_secret_value() if self.eventhub_connection else None

    def problems(self) -> list[str]:
        """What is missing for the chosen feed (empty when usable)."""
        if self.feed == "file" or self.eventhub_connection:
            return []
        missing = [
            name
            for name, value in (
                ("PITCH_LEDGER_EVENTHUB_NAMESPACE", self.eventhub_namespace),
                ("PITCH_LEDGER_EVENTHUB_NAME", self.eventhub_name),
            )
            if not value
        ]
        if not missing:
            return []
        return [f"eventhubs feed needs {' and '.join(missing)} (or a connection string)"]

    def describe(self) -> str:
        """Where the feed goes, without secrets."""
        if self.feed == "file":
            return f"file {self.feed_file}"
        if connection := self.connection():
            parts = dict(p.split("=", 1) for p in connection.split(";") if "=" in p)
            host = urlparse(parts.get("Endpoint", "")).hostname or "?"
            hub = parts.get("EntityPath") or self.eventhub_name or "?"
            return f"eventhubs {host}/{hub} (connection string)"
        from pitch_ledger.infra_adapters.eventhubs import fully_qualified

        return (
            f"eventhubs {fully_qualified(self.eventhub_namespace or '')}/{self.eventhub_name} "
            "(Entra ID)"
        )


def _checked(settings: FeedSettings) -> FeedSettings:
    if problems := settings.problems():
        raise ValueError("; ".join(problems))
    return settings


def open_publisher(settings: FeedSettings) -> FeedPublisher:
    if _checked(settings).feed == "file":
        return FilePublisher(settings.feed_file)
    from pitch_ledger.infra_adapters.eventhubs import EventHubsPublisher

    return EventHubsPublisher.connect(
        settings.eventhub_namespace, settings.eventhub_name, settings.connection()
    )


def open_subscriber(settings: FeedSettings, from_start: bool = False) -> FeedSubscriber:
    if _checked(settings).feed == "file":
        return FileSubscriber(settings.feed_file, from_start=from_start)
    from pitch_ledger.infra_adapters.eventhubs import EventHubsSubscriber

    return EventHubsSubscriber.connect(
        settings.eventhub_namespace,
        settings.eventhub_name,
        settings.connection(),
        settings.eventhub_consumer_group,
        from_start=from_start,
    )
