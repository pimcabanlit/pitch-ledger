"""Cosmos DB ledger and Service Bus queue adapters, with fakes that behave like the services."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pitch_ledger.agents.queue import InMemoryTriggerQueue
from pitch_ledger.agents.replay import ReplayOptions, replay_match
from pitch_ledger.cli import main
from pitch_ledger.infra_adapters.cosmos import CosmosLedger
from pitch_ledger.infra_adapters.ledger_queue import LedgerQueueSettings, open_ledger, open_queue
from pitch_ledger.infra_adapters.servicebus import ServiceBusTriggerQueue
from pitch_ledger.ops import OpsDesk
from pitch_ledger.schema import InsightSubject, Trigger


class FakeContainer:
    """Documents keyed by (partition key, id), like a Cosmos container."""

    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict[str, Any]] = {}

    def upsert_item(self, body: dict[str, Any]) -> None:
        self.docs[(body["match_id"], body["id"])] = {**body, "_etag": "x"}

    def query_items(self, query: str, parameters: list[dict[str, Any]], partition_key: str) -> Any:
        return [d for (pk, _), d in self.docs.items() if pk == partition_key]

    def delete_item(self, item: str, partition_key: str) -> None:
        del self.docs[(partition_key, item)]


@pytest.fixture(scope="module")
def match_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("data") / "M0002"
    assert main(["generate", "--scenario", "late_collapse", "--seed", "4212",
                 "--out", str(out)]) == 0  # fmt: skip
    return out


def _trigger(n: int, match_id: str = "M0002") -> Trigger:
    return Trigger(trigger_id=f"{match_id}-T{n:04d}", match_id=match_id, period=2,
                   clock_ms=n * 1000, insight_type="momentum_shift", rule_id="momentum_shift",
                   subject=InsightSubject(team_id="T02"), metrics={"control_after": 6},
                   evidence_event_ids=[f"{match_id}-E000010"])  # fmt: skip


def test_replay_through_the_cosmos_ledger_and_reload(match_dir: Path, tmp_path: Path) -> None:
    container = FakeContainer()
    ledger = CosmosLedger(container, fresh_matches=("M0002",))
    options = ReplayOptions(approve=True, inject="first", provider="offline", stats="local",
                            only=("momentum_shift",), ledger_dir=tmp_path / "ledger")  # fmt: skip
    run = asyncio.run(replay_match(match_dir, options, ledger, InMemoryTriggerQueue()))
    insights = run.ledger.insights("M0002")
    assert [i.audit_status.value for i in insights] == ["corrected"]
    kinds = sorted({d["kind"] for d in container.docs.values()})
    assert kinds == ["audit", "insight", "overlay", "story", "trigger"]
    assert ledger.writes >= len(container.docs)  # upserts can rewrite a document
    assert (tmp_path / "ledger" / "run.json").exists()

    reloaded = CosmosLedger(container)  # a second process: reads the match back
    assert reloaded.get_insight(insights[0].insight_id) is None  # not loaded until touched
    assert reloaded.next_id("M0002", "I") == "M0002-I0002"  # counters continue
    assert reloaded.get_insight(insights[0].insight_id) == insights[0]
    assert len(reloaded.overlays()) == len(run.ledger.overlays())
    story = run.ledger.story(insights[0].insight_id)
    assert story is not None and reloaded.story(story.insight_id) == story
    assert (story.match_id, f"{story.insight_id}-story") in container.docs
    alert = OpsDesk(reloaded).raise_alert("M0002", "feed_gap", "warning", "gap", ["M0002-E000001"])
    assert alert is not None and alert.alert_id == "M0002-X0001"
    again = CosmosLedger(container)
    again.load("M0002")
    assert again.alerts("M0002") == [alert] and again.next_id("M0002", "X") == "M0002-X0002"


def test_fresh_replay_empties_only_its_match() -> None:
    container = FakeContainer()
    first = CosmosLedger(container)
    first.save_trigger(_trigger(1))
    first.save_trigger(_trigger(1, "M0003"))
    fresh = CosmosLedger(container, fresh_matches=("M0002",))
    assert fresh.next_id("M0002", "I") == "M0002-I0001"
    assert [pk for pk, _ in container.docs] == ["M0003"]


class Msg:
    def __init__(self, body: str, message_id: str) -> None:
        self.body, self.message_id = body, message_id
        self.delivery_count = 0
        self.dead_letter_reason: str | None = None

    def __str__(self) -> str:
        return self.body


class FakeBus:
    """A queue with max delivery count 3 and a dead-letter sub-queue."""

    def __init__(self) -> None:
        self.ready: list[Msg] = []
        self.dlq: list[Msg] = []

    # sender
    def send_messages(self, message: Any) -> None:
        self.ready.append(Msg(str(message), message.message_id))

    # receiver
    def receive_messages(self, max_message_count: int, max_wait_time: float) -> list[Msg]:
        while self.ready:
            msg = self.ready.pop(0)
            if msg.delivery_count >= 3:
                msg.dead_letter_reason = "MaxDeliveryCountExceeded"
                self.dlq.append(msg)
                continue
            msg.delivery_count += 1
            return [msg]
        return []

    def complete_message(self, msg: Msg) -> None:
        pass

    def abandon_message(self, msg: Msg) -> None:
        self.ready.insert(0, msg)

    def dead_letter_message(self, msg: Msg, reason: str, error_description: str) -> None:
        msg.dead_letter_reason = reason
        self.dlq.append(msg)

    def peek_messages(self, max_message_count: int) -> list[Msg]:
        return list(self.dlq)


def test_service_bus_queue_retries_dead_letters_and_skips_stale_messages() -> None:
    bus = FakeBus()
    bus.ready.append(Msg(_trigger(9, "M0001").model_dump_json(), "M0001-T0009"))  # stale
    queue = ServiceBusTriggerQueue(bus, bus, bus, max_wait_s=0)
    queue.send(_trigger(1))
    queue.send(_trigger(2))
    first = queue.receive()
    assert first is not None and first.message_id == "M0002-T0001" and first.delivery_count == 1
    queue.complete(first)
    for attempt in range(1, 4):  # fails every time
        delivery = queue.receive()
        assert delivery is not None and delivery.delivery_count == attempt
        queue.abandon(delivery, f"boom {attempt}")
    assert queue.receive() is None
    dead = queue.dead_letters()
    assert [d.message_id for d in dead] == ["M0002-T0002"]  # the stale one is not this run's
    assert dead[0].errors == ["boom 1", "boom 2", "boom 3", "MaxDeliveryCountExceeded"]
    assert [m.dead_letter_reason for m in bus.dlq] == ["stale", "MaxDeliveryCountExceeded"]


def test_a_trigger_is_marked_ours_before_it_is_sent() -> None:
    """A receiver thread can get the message before send_messages returns."""
    bus = FakeBus()
    queue = ServiceBusTriggerQueue(bus, bus, bus, max_wait_s=0)
    seen_at_send: list[bool] = []
    original = bus.send_messages

    def send_and_receive_at_once(message: Any) -> None:
        original(message)
        seen_at_send.append(message.message_id in queue._sent)  # what a racing receiver sees

    bus.send_messages = send_and_receive_at_once  # type: ignore[method-assign]
    queue.send(_trigger(1))
    assert seen_at_send == [True]
    delivery = queue.receive()
    assert delivery is not None and bus.dlq == []


def test_defaults_are_local_and_cloud_adapters_need_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("PITCH_LEDGER_LEDGER", "PITCH_LEDGER_QUEUE", "COSMOS_ENDPOINT",
                 "PITCH_LEDGER_COSMOS_ENDPOINT", "PITCH_LEDGER_SERVICEBUS_NAMESPACE",
                 "PITCH_LEDGER_SERVICEBUS_CONNECTION"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    settings = LedgerQueueSettings(_env_file=None)  # type: ignore[call-arg]
    assert open_ledger(settings, "M0002") is None
    assert isinstance(open_queue(settings), InMemoryTriggerQueue)
    with pytest.raises(ValueError, match="COSMOS_ENDPOINT"):
        open_ledger(LedgerQueueSettings(_env_file=None, ledger="cosmos"), "M0002")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="SERVICEBUS_NAMESPACE"):
        open_queue(LedgerQueueSettings(_env_file=None, queue="servicebus"))  # type: ignore[call-arg]
