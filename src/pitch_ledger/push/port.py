"""Live push to Studio clients.

The server sends small signals ("overlays changed for M0002", "insight M0002-I0016 is now
aired") to a group per match and audience; clients then re-read through the API, so what a
fan may see is still decided in one place (aired overlays only). ``Notifier`` is the
interface: ``NullNotifier`` when push is off (clients poll), the Azure Web PubSub adapter in
``pitch_ledger.infra_adapters.webpubsub``. Notifications fail open: the data is in the
ledger either way and clients also poll.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Audience = Literal["fans", "producers"]


def group_name(match_id: str, audience: Audience) -> str:
    return f"{audience}.{match_id}"


class Notifier(Protocol):
    def notify(self, match_id: str, audience: Audience, event: str, data: dict[str, Any]) -> None:
        """Send ``{"event", "match_id", **data}`` to the match's group for that audience."""
        ...

    def client_url(self, match_id: str, audience: Audience) -> str | None:
        """A short-lived WebSocket URL that joins the group (None when push is off)."""
        ...


class NullNotifier:
    def notify(self, match_id: str, audience: Audience, event: str, data: dict[str, Any]) -> None:
        pass

    def client_url(self, match_id: str, audience: Audience) -> str | None:
        return None


@dataclass
class RecordingNotifier:
    """Keeps every notification (tests)."""

    sent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def notify(self, match_id: str, audience: Audience, event: str, data: dict[str, Any]) -> None:
        self.sent.append(
            (group_name(match_id, audience), {"event": event, "match_id": match_id, **data})
        )

    def client_url(self, match_id: str, audience: Audience) -> str | None:
        return f"wss://push.test/{group_name(match_id, audience)}"
