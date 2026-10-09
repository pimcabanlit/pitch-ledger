"""Azure Web PubSub push adapter (Free tier: 20 connections, 20,000 messages a day).

Hub ``studio``; one group per match and audience (``fans.M0002``, ``producers.M0002``).
Client URLs are signed by the service for 60 minutes and join exactly one group, so a fan
can only receive their match's fan signals. Sign-in: Microsoft Entra ID with the *Web
PubSub Service Owner* role, or a connection string. Sends fail open (logged).
"""

import json
import logging
from typing import Any, Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from pitch_ledger.core.checks import Check, short_error
from pitch_ledger.push.port import Audience, Notifier, NullNotifier, group_name

log = logging.getLogger(__name__)
TOKEN_MINUTES = 60


class PushSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PITCH_LEDGER_", env_file=".env", extra="ignore")

    push: Literal["off", "webpubsub"] = "off"
    webpubsub_endpoint: str | None = None  # https://<name>.webpubsub.azure.com
    webpubsub_hub: str = "studio"
    webpubsub_connection: SecretStr | None = None  # connection string (secret)

    def describe(self) -> str:
        if self.push == "off":
            return "off (clients poll)"
        return (
            f"webpubsub {self.webpubsub_endpoint or 'connection string'} hub {self.webpubsub_hub}"
        )


class WebPubSubNotifier:
    def __init__(self, client: Any) -> None:
        """``client`` is a ``WebPubSubServiceClient`` (tests pass a fake)."""
        self._client = client

    @classmethod
    def connect(cls, settings: PushSettings) -> "WebPubSubNotifier":
        from azure.messaging.webpubsubservice import WebPubSubServiceClient

        if settings.webpubsub_connection:
            client = WebPubSubServiceClient.from_connection_string(
                settings.webpubsub_connection.get_secret_value(), hub=settings.webpubsub_hub
            )
        else:
            from azure.identity import DefaultAzureCredential

            client = WebPubSubServiceClient(
                endpoint=settings.webpubsub_endpoint or "",
                hub=settings.webpubsub_hub,
                credential=DefaultAzureCredential(),
            )
        return cls(client)

    def send(self, match_id: str, audience: Audience, event: str, data: dict[str, Any]) -> None:
        """Send one signal; raises on failure (``notify`` is the fail-open version)."""
        message = json.dumps({"event": event, "match_id": match_id, **data})
        self._client.send_to_group(
            group_name(match_id, audience), message, content_type="application/json"
        )

    def notify(self, match_id: str, audience: Audience, event: str, data: dict[str, Any]) -> None:
        try:
            self.send(match_id, audience, event, data)
        except Exception as exc:  # fail open: clients also poll
            log.warning("push to %s failed: %s", group_name(match_id, audience), short_error(exc))

    def client_url(self, match_id: str, audience: Audience) -> str | None:
        token = self._client.get_client_access_token(
            groups=[group_name(match_id, audience)], minutes_to_expire=TOKEN_MINUTES
        )
        return str(token["url"])


def open_notifier(settings: PushSettings) -> Notifier:
    if settings.push == "off":
        return NullNotifier()
    if not (settings.webpubsub_endpoint or settings.webpubsub_connection):
        raise ValueError("webpubsub push needs PITCH_LEDGER_WEBPUBSUB_ENDPOINT")
    return WebPubSubNotifier.connect(settings)


def check_push(settings: PushSettings) -> list[Check]:
    """Settings, sign-in (a client URL signed by the service) and one test send."""
    where = settings.webpubsub_endpoint or (
        "connection string" if settings.webpubsub_connection else None
    )
    checks = [Check("PITCH_LEDGER_WEBPUBSUB_ENDPOINT", bool(where), where or "not set")]
    if not where:
        return checks
    try:
        notifier = WebPubSubNotifier.connect(settings)
        url = notifier.client_url("M9999", "fans")
        checks.append(
            Check("client URL", bool(url and url.startswith("wss://")), "signed by the service")
        )
        notifier.send("M9999", "fans", "check", {})
        checks.append(Check(f"send to hub {settings.webpubsub_hub}", True, "accepted"))
    except Exception as exc:  # sign-in, role or network
        checks.append(Check("Web PubSub", False, short_error(exc)))
    return checks
