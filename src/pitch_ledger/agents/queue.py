"""The trigger queue between the detector and the orchestrator.

``TriggerQueue`` is the interface; the in-memory queue gives each message up to
``max_deliveries`` attempts and then dead-letters it, like the Service Bus queue that
replaces it in the cloud (Phase 4).
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from pitch_ledger.schema import Trigger


@dataclass
class Delivery:
    message_id: str
    trigger: Trigger
    delivery_count: int = 0
    errors: list[str] = field(default_factory=list)


class TriggerQueue(Protocol):
    def send(self, trigger: Trigger) -> None: ...

    def receive(self) -> Delivery | None: ...

    def complete(self, delivery: Delivery) -> None: ...

    def abandon(self, delivery: Delivery, reason: str) -> None: ...

    def dead_letters(self) -> list[Delivery]: ...


class InMemoryTriggerQueue:
    def __init__(self, max_deliveries: int = 3) -> None:
        self.max_deliveries = max_deliveries
        self._ready: deque[Delivery] = deque()
        self._dead: list[Delivery] = []
        self.completed: list[Delivery] = []

    def __len__(self) -> int:
        return len(self._ready)

    def send(self, trigger: Trigger) -> None:
        self._ready.append(Delivery(trigger.trigger_id, trigger))

    def receive(self) -> Delivery | None:
        if not self._ready:
            return None
        delivery = self._ready.popleft()
        delivery.delivery_count += 1
        return delivery

    def complete(self, delivery: Delivery) -> None:
        self.completed.append(delivery)

    def abandon(self, delivery: Delivery, reason: str) -> None:
        delivery.errors.append(reason)
        if delivery.delivery_count >= self.max_deliveries:
            self._dead.append(delivery)
        else:
            self._ready.append(delivery)

    def dead_letters(self) -> list[Delivery]:
        return list(self._dead)
