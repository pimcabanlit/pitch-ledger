"""Web PubSub push adapter with a fake service client."""

import json
from typing import Any

import pytest

from pitch_ledger.infra_adapters.webpubsub import PushSettings, WebPubSubNotifier, open_notifier
from pitch_ledger.push import NullNotifier


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, dict[str, Any], str]] = []
        self.tokens: list[dict[str, Any]] = []
        self.fail = fail

    def send_to_group(self, group: str, message: str, content_type: str) -> None:
        if self.fail:
            raise RuntimeError("service unavailable")
        self.sent.append((group, json.loads(message), content_type))

    def get_client_access_token(self, **kwargs: Any) -> dict[str, str]:
        self.tokens.append(kwargs)
        return {
            "url": f"wss://x.webpubsub.azure.com/client/hubs/studio?access_token=t-{kwargs['groups'][0]}"
        }


def test_notify_sends_a_json_signal_to_the_match_group() -> None:
    client = FakeClient()
    WebPubSubNotifier(client).notify(
        "M0002", "fans", "overlays_changed", {"insight_id": "M0002-I0016"}
    )
    signal = {"event": "overlays_changed", "match_id": "M0002", "insight_id": "M0002-I0016"}
    assert client.sent == [("fans.M0002", signal, "application/json")]


def test_notify_fails_open_but_send_raises() -> None:
    notifier = WebPubSubNotifier(FakeClient(fail=True))
    notifier.notify("M0002", "producers", "insight_changed", {})  # logged, not raised
    with pytest.raises(RuntimeError):
        notifier.send("M0002", "producers", "insight_changed", {})


def test_client_url_joins_exactly_one_group_for_an_hour() -> None:
    client = FakeClient()
    url = WebPubSubNotifier(client).client_url("M0002", "fans")
    assert url is not None and url.startswith("wss://") and url.endswith("fans.M0002")
    assert client.tokens == [{"groups": ["fans.M0002"], "minutes_to_expire": 60}]


def test_push_is_off_by_default_and_needs_an_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PITCH_LEDGER_PUSH", "PITCH_LEDGER_WEBPUBSUB_ENDPOINT",
                 "PITCH_LEDGER_WEBPUBSUB_CONNECTION"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    assert isinstance(open_notifier(PushSettings(_env_file=None)), NullNotifier)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="WEBPUBSUB_ENDPOINT"):
        open_notifier(PushSettings(_env_file=None, push="webpubsub"))  # type: ignore[call-arg]
