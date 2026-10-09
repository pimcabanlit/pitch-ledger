"""Azure Service Bus trigger-queue adapter (Basic tier): the cloud version of
``InMemoryTriggerQueue``, with the same interface.

- One queue (``triggers``); the message body is the trigger JSON, the message ID its
  trigger ID. Service Bus counts deliveries and moves a message to the dead-letter
  sub-queue after the queue's max delivery count (set it to 3, like the in-memory queue).
- Locks are renewed while a trigger is processed (model calls can take a while).
- A message this queue object did not send (left over from an earlier, crashed run) is
  dead-lettered with reason ``stale``, so it is visible rather than processed with the
  wrong match context.
- Sign-in: Microsoft Entra ID (*Azure Service Bus Data Sender* and *Data Receiver*) or a
  connection string. Transport: plain AMQP, or WebSockets on 443 behind a proxy.
"""

import os
from typing import Any, Literal
from urllib.parse import urlparse

from pitch_ledger.agents.queue import Delivery
from pitch_ledger.core.checks import Check, short_error
from pitch_ledger.schema import Trigger

Transport = Literal["auto", "amqp", "websockets"]
LOCK_RENEWAL_S = 600
CHECK_MATCH = "M9999"  # reserved for setup checks, never a real match


def client_options(transport: Transport = "auto") -> dict[str, Any]:
    from azure.servicebus import TransportType

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if transport == "amqp" or (transport == "auto" and not proxy):
        return {"transport_type": TransportType.Amqp}
    options: dict[str, Any] = {"transport_type": TransportType.AmqpOverWebsocket}
    if proxy:
        url = urlparse(proxy)
        options["http_proxy"] = {"proxy_hostname": url.hostname, "proxy_port": url.port or 80}
    return options


def fully_qualified(namespace: str) -> str:
    host = (
        urlparse(namespace).hostname if "//" in namespace else namespace.strip("/")
    ) or namespace
    return host if "." in host else f"{host}.servicebus.windows.net"


class ServiceBusTriggerQueue:
    def __init__(
        self,
        sender: Any,
        receiver: Any,
        dead_letter_receiver: Any,
        lock_renewer: Any = None,
        max_wait_s: float = 5.0,
        on_close: Any = None,
    ) -> None:
        """SDK sender and receivers (tests pass fakes)."""
        self._sender, self._receiver, self._dlq = sender, receiver, dead_letter_receiver
        self._renewer = lock_renewer
        self._max_wait_s = max_wait_s
        self._on_close = on_close
        self._sent: set[str] = set()
        self._messages: dict[str, Any] = {}
        self._errors: dict[str, list[str]] = {}

    @classmethod
    def connect(
        cls,
        namespace: str | None,
        queue: str,
        connection: str | None = None,
        transport: Transport = "auto",
    ) -> "ServiceBusTriggerQueue":
        from azure.servicebus import AutoLockRenewer, ServiceBusClient, ServiceBusSubQueue

        credential: Any = None
        if connection:
            client = ServiceBusClient.from_connection_string(
                connection, **client_options(transport)
            )
        else:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
            client = ServiceBusClient(
                fully_qualified(namespace or ""), credential, **client_options(transport)
            )
        renewer = AutoLockRenewer(max_lock_renewal_duration=LOCK_RENEWAL_S)
        sender = client.get_queue_sender(queue)
        receiver = client.get_queue_receiver(queue)
        dlq = client.get_queue_receiver(queue, sub_queue=ServiceBusSubQueue.DEAD_LETTER)

        def close() -> None:
            renewer.close()
            for part in (sender, receiver, dlq, client):
                part.close()
            if credential is not None:
                credential.close()

        return cls(sender, receiver, dlq, renewer, on_close=close)

    def send(self, trigger: Trigger) -> None:
        from azure.servicebus import ServiceBusMessage

        message = ServiceBusMessage(
            trigger.model_dump_json(),
            message_id=trigger.trigger_id,
            content_type="application/json",
            subject=trigger.match_id,
        )
        self._sender.send_messages(message)
        self._sent.add(trigger.trigger_id)

    def receive(self) -> Delivery | None:
        while True:
            batch = self._receiver.receive_messages(
                max_message_count=1, max_wait_time=self._max_wait_s
            )
            if not batch:
                return None
            message = batch[0]
            if message.message_id not in self._sent:
                self._receiver.dead_letter_message(
                    message, reason="stale", error_description="not sent by this run"
                )
                continue
            if self._renewer is not None:
                self._renewer.register(self._receiver, message)
            self._messages[message.message_id] = message
            trigger = Trigger.model_validate_json(str(message))
            return Delivery(
                message.message_id,
                trigger,
                delivery_count=message.delivery_count or 1,
                errors=list(self._errors.get(message.message_id, [])),
            )

    def complete(self, delivery: Delivery) -> None:
        self._receiver.complete_message(self._messages.pop(delivery.message_id))

    def abandon(self, delivery: Delivery, reason: str) -> None:
        self._errors.setdefault(delivery.message_id, []).append(reason)
        self._receiver.abandon_message(self._messages.pop(delivery.message_id))

    def dead_letters(self) -> list[Delivery]:
        """This run's dead-lettered triggers (peeked, left in the dead-letter queue)."""
        found = []
        for message in self._dlq.peek_messages(max_message_count=250):
            if message.message_id in self._sent:
                errors = self._errors.get(message.message_id, [])
                reason = message.dead_letter_reason
                found.append(
                    Delivery(
                        message.message_id,
                        Trigger.model_validate_json(str(message)),
                        delivery_count=message.delivery_count or 0,
                        errors=[*errors, *([reason] if reason else [])],
                    )
                )
        return found

    def close(self) -> None:
        if self._on_close:
            self._on_close()


def check_queue(
    namespace: str | None, queue: str, connection: str | None, transport: Transport = "auto"
) -> list[Check]:
    """Settings, sign-in, and one probe trigger sent, received and completed."""
    where = namespace or ("connection string" if connection else None)
    checks = [Check("PITCH_LEDGER_SERVICEBUS_NAMESPACE", bool(where), where or "not set")]
    if not where:
        return checks
    import time

    from pitch_ledger.schema import InsightSubject

    probe = Trigger(
        trigger_id=f"{CHECK_MATCH}-T{int(time.time()) % 10_000:04d}",
        match_id=CHECK_MATCH,
        period=1,
        clock_ms=0,
        insight_type="recap",
        rule_id="queue_check",
        subject=InsightSubject(),
        metrics={},
        evidence_event_ids=[],
    )
    try:
        q = ServiceBusTriggerQueue.connect(namespace, queue, connection, transport)
        started = time.monotonic()
        q.send(probe)
        delivery = q.receive()
        if delivery is None or delivery.message_id != probe.trigger_id:
            checks.append(Check(f"queue {queue}", False, "probe not received within 5 s"))
        else:
            q.complete(delivery)
            took = time.monotonic() - started
            checks.append(Check(f"queue {queue}", True, f"send, receive, complete in {took:.1f} s"))
        q.close()
    except Exception as exc:  # sign-in, permission, missing queue or network
        checks.append(Check(f"queue {queue}", False, short_error(exc)))
    return checks
