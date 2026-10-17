"""In-memory ledger (tests, and the base of the local file ledger)."""

from collections import Counter

from pitch_ledger.ledger.repository import IdKind
from pitch_ledger.schema import AuditRecord, Insight, OpsAlert, Overlay, StoryRecord, Trigger


class InMemoryLedger:
    def __init__(self) -> None:
        self._counters: Counter[tuple[str, str]] = Counter()
        self.triggers: dict[str, Trigger] = {}
        self._insights: dict[str, Insight] = {}
        self._audits: list[AuditRecord] = []
        self._overlays: dict[str, Overlay] = {}
        self._stories: dict[str, StoryRecord] = {}
        self._alerts: dict[str, OpsAlert] = {}

    def load(self, match_id: str) -> None:
        """Bring a match's stored records in (and empty it, for a fresh run); a no-op here,
        overridden by stores that read on first touch."""

    def next_id(self, match_id: str, kind: IdKind) -> str:
        self._counters[(match_id, kind)] += 1
        return f"{match_id}-{kind}{self._counters[(match_id, kind)]:04d}"

    def save_trigger(self, trigger: Trigger) -> None:
        self.triggers[trigger.trigger_id] = trigger

    def get_trigger(self, trigger_id: str) -> Trigger | None:
        return self.triggers.get(trigger_id)

    def put_insight(self, insight: Insight) -> None:
        self._insights[insight.insight_id] = insight

    def get_insight(self, insight_id: str) -> Insight | None:
        return self._insights.get(insight_id)

    def insights(self, match_id: str | None = None) -> list[Insight]:
        return [i for i in self._insights.values() if match_id in (None, i.match_id)]

    def add_audit(self, record: AuditRecord) -> None:
        self._audits.append(record)

    def audits(self, insight_id: str | None = None) -> list[AuditRecord]:
        return [a for a in self._audits if insight_id in (None, a.insight_id)]

    def put_overlay(self, overlay: Overlay) -> None:
        self._overlays[overlay.overlay_id] = overlay

    def overlays(self, insight_id: str | None = None) -> list[Overlay]:
        return [o for o in self._overlays.values() if insight_id in (None, o.insight_id)]

    def put_story(self, story: StoryRecord) -> None:
        self._stories[story.insight_id] = story

    def story(self, insight_id: str) -> StoryRecord | None:
        return self._stories.get(insight_id)

    def stories(self, match_id: str | None = None) -> list[StoryRecord]:
        return [s for s in self._stories.values() if match_id in (None, s.match_id)]

    def put_alert(self, alert: OpsAlert) -> None:
        self._alerts[alert.alert_id] = alert

    def get_alert(self, alert_id: str) -> OpsAlert | None:
        return self._alerts.get(alert_id)

    def alerts(self, match_id: str | None = None) -> list[OpsAlert]:
        return [a for a in self._alerts.values() if match_id in (None, a.match_id)]
