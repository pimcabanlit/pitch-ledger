"""Raise and acknowledge ops alerts, and the rules that read them off the ledger.

``OpsDesk`` writes each alert three ways: the ledger (system of record), an ``OpsAlerts``
row in the event store (the dashboard) and an ``alert_changed`` signal to the match's
producers (the Studio console re-reads). An acknowledgement updates the ledger record and
appends a second store row with ``acknowledged_by`` (the store only appends).

Rules here read what the agents already recorded, so alerts never re-judge an insight:

- ``review_insight``: an Auditor ``block`` record -> insight blocked; ``fallback`` records
  (template copy) -> model fallback, a warning when the call budget ran out.
- ``dead_lettered``: a trigger that failed every delivery attempt.

Feed rules (gaps, lag, validation) live in ``ops.feed``.
"""

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from pitch_ledger.engine.windows import match_minute
from pitch_ledger.eventstore.port import EventStore
from pitch_ledger.eventstore.tables import project
from pitch_ledger.ledger import InMemoryLedger
from pitch_ledger.push import Notifier, NullNotifier
from pitch_ledger.schema import OpsAlert, Trigger
from pitch_ledger.schema.ops import AlertKind, Severity

log = logging.getLogger(__name__)
MAX_MESSAGE = 500
MAX_REFS = 50


def minute_label(period: int, clock_ms: int) -> str:
    """As the insight copy and the Studio write it: 68.86 -> 69'."""
    return f"{round(match_minute(period, clock_ms), 1):.0f}'"


def _clip(text: str) -> str:
    return text if len(text) <= MAX_MESSAGE else text[: MAX_MESSAGE - 1] + "…"


def ops_alert_row(alert: OpsAlert) -> dict[str, Any]:
    return project("OpsAlerts", alert.model_dump(mode="json"))


class OpsDesk:
    def __init__(
        self,
        ledger: InMemoryLedger,
        store: EventStore | None = None,
        notifier: Notifier | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.ledger, self.store, self.now = ledger, store, now
        self.notifier: Notifier = notifier or NullNotifier()

    def raise_alert(
        self,
        match_id: str,
        kind: AlertKind,
        severity: Severity,
        message: str,
        refs: Iterable[str] = (),
    ) -> OpsAlert | None:
        """Record a new alert. With ``refs``, an alert of the same kind and refs is raised
        only once (a re-run or a second look at the same insight adds nothing)."""
        refs = list(refs)[:MAX_REFS]
        if refs and any(a.kind == kind and a.refs == refs for a in self.ledger.alerts(match_id)):
            return None
        alert = OpsAlert(alert_id=self.ledger.next_id(match_id, "X"), match_id=match_id,
                         kind=kind, severity=severity, message=_clip(message), refs=refs,
                         raised_at=self.now())  # fmt: skip
        self.ledger.put_alert(alert)
        self._record(alert)
        log.warning("ops alert %s %s (%s): %s", alert.alert_id, kind, severity, alert.message)
        return alert

    def acknowledge(self, alert_id: str, producer: str) -> OpsAlert | None:
        """Mark an alert as seen by ``producer``; acknowledging twice keeps the first."""
        alert = self.ledger.get_alert(alert_id)
        if alert is None or alert.acknowledged_by is not None:
            return alert
        update = {"acknowledged_by": producer, "acknowledged_at": self.now()}
        alert = alert.model_copy(update=update)
        self.ledger.put_alert(alert)
        self._record(alert)
        return alert

    def _record(self, alert: OpsAlert) -> None:
        if self.store is not None:
            self.store.append("OpsAlerts", [ops_alert_row(alert)])
        signal = {"alert_id": alert.alert_id, "kind": alert.kind, "severity": alert.severity,
                  "acknowledged": alert.acknowledged_by is not None}  # fmt: skip
        self.notifier.notify(alert.match_id, "producers", "alert_changed", signal)


def review_insight(desk: OpsDesk, insight_id: str | None) -> list[OpsAlert]:
    """Alerts for what the audit trail of one insight shows (after a trigger or decision)."""
    insight = desk.ledger.get_insight(insight_id or "")
    if insight is None:
        return []
    audits = desk.ledger.audits(insight.insight_id)
    where = f"{insight.insight_id} at {minute_label(insight.period, insight.clock_ms)}"
    raised: list[OpsAlert | None] = []
    blocks = [a for a in audits if a.action == "block"]
    if blocks:
        why = "; ".join(blocks[-1].findings) or "failed its checks"
        raised.append(desk.raise_alert(
            insight.match_id, "insight_blocked", "warning",
            f"Auditor blocked {where} ({insight.insight_type.value}): {why}. It will not air.",
            [insight.insight_id],
        ))  # fmt: skip
    seen = {r for a in desk.ledger.alerts(insight.match_id) if a.kind == "model_fallback"
            for r in a.refs}  # fmt: skip
    fallbacks = [a for a in audits if a.action == "fallback" and a.audit_id not in seen]
    if fallbacks:
        findings = [f for a in fallbacks for f in a.findings]
        budget = any("budget spent" in f for f in findings)
        steps = ", ".join(sorted({a.actor for a in fallbacks}))
        lead = "Model call budget spent: " if budget else ""
        raised.append(desk.raise_alert(
            insight.match_id, "model_fallback", "warning" if budget else "info",
            f"{lead}template copy used for {where} ({steps}): {'; '.join(findings[:3])}",
            [insight.insight_id, *(a.audit_id for a in fallbacks)],
        ))  # fmt: skip
    return [a for a in raised if a is not None]


def dead_lettered(
    desk: OpsDesk, trigger: Trigger, deliveries: int, errors: list[str]
) -> OpsAlert | None:
    last = errors[-1] if errors else "no error recorded"
    return desk.raise_alert(
        trigger.match_id, "trigger_dead_lettered", "critical",
        f"Trigger {trigger.trigger_id} ({trigger.rule_id} at "
        f"{minute_label(trigger.period, trigger.clock_ms)}) failed {deliveries} delivery "
        f"attempts and went to the dead-letter queue: {last}",
        [trigger.trigger_id],
    )  # fmt: skip
