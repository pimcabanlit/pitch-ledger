"""Feed rules for the live service (docs/architecture.md, Ops alerts).

- **Feed gap:** no message for ``gap_s`` (10 s) while a period is being played; one alert
  per gap (half time is not a gap).
- **Feed lag:** a wall-clock minute whose p95 send-to-receive latency is above
  ``lag_p95_ms`` (5 s), from the same per-minute rows as the ``FeedHealth`` table.
- **Validation:** rejected rows (dropped before the engine) are summarized at every tick;
  flagged rows (passed with a warning) at most once a minute.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pitch_ledger.ops.alerts import OpsDesk
from pitch_ledger.schema import OpsAlert
from pitch_ledger.validation import RuleAction, ValidationResult


def _summary(results: list[ValidationResult]) -> str:
    counts = Counter(r.rule_id for r in results)
    rules = ", ".join(f"{rule} x{n}" for rule, n in sorted(counts.items()))
    first = results[0]
    return f"{rules}; first: {first.event_id or 'row'}: {first.message}"


@dataclass
class FeedWatch:
    desk: OpsDesk
    gap_s: float = 10.0
    lag_p95_ms: float = 5000.0
    flag_every_s: float = 60.0
    _last_seen: dict[str, float] = field(default_factory=dict)
    _in_gap: set[str] = field(default_factory=set)
    _rejects: dict[str, list[ValidationResult]] = field(default_factory=lambda: defaultdict(list))
    _flags: dict[str, list[ValidationResult]] = field(default_factory=lambda: defaultdict(list))
    _flagged_at: dict[str, float] = field(default_factory=dict)

    def seen(self, match_id: str, now: float) -> None:
        """A feed message for the match arrived (ends any gap)."""
        self._last_seen[match_id] = now
        self._in_gap.discard(match_id)

    def check_gap(
        self, match_id: str, now: float, in_play: bool, where: str, last_ref: str | None
    ) -> OpsAlert | None:
        last = self._last_seen.get(match_id)
        if last is None or not in_play or match_id in self._in_gap or now - last < self.gap_s:
            return None
        self._in_gap.add(match_id)
        return self.desk.raise_alert(
            match_id, "feed_gap", "warning",
            f"No feed message for {round(now - last, 1):g} s during play (last at {where}). "
            "Insights wait for the feed; check the feed source and the Event Hubs namespace.",
            [last_ref] if last_ref else [],
        )  # fmt: skip

    def check_lag(self, health_rows: Iterable[dict[str, Any]]) -> list[OpsAlert]:
        """Alerts for finished per-minute feed-health rows (``FeedHealthMeter.take``)."""
        raised = []
        for row in health_rows:
            p95 = row.get("p95_ms")
            if p95 is None or p95 <= self.lag_p95_ms:
                continue
            start = datetime.fromisoformat(row["window_start"]).strftime("%H:%M")
            alert = self.desk.raise_alert(
                row["match_id"], "feed_lag", "warning",
                f"Feed lag: p95 {p95 / 1000:.1f} s (max {row['max_ms'] / 1000:.1f} s) over "
                f"{row['messages']} messages in the minute from {start} UTC "
                f"(limit {self.lag_p95_ms / 1000:g} s). Overlays reach viewers late.",
            )  # fmt: skip
            if alert is not None:
                raised.append(alert)
        return raised

    def validated(self, match_id: str, results: Iterable[ValidationResult]) -> None:
        """Results of one row's validation (kept until the next ``flush``)."""
        for r in results:
            target = self._rejects if r.action is RuleAction.REJECT else self._flags
            target[match_id].append(r)

    def flush(self, now: float, final: bool = False) -> list[OpsAlert]:
        """Rejects since the last call as one alert per match; flags once a minute (and at
        the end)."""
        raised: list[OpsAlert | None] = []
        for match_id, rejects in self._rejects.items():
            if rejects:
                refs = sorted({r.event_id for r in rejects if r.event_id})
                raised.append(self.desk.raise_alert(
                    match_id, "validation_reject", "warning",
                    f"{len(rejects)} feed row(s) rejected and dropped before the engine: "
                    f"{_summary(rejects)}", refs,
                ))  # fmt: skip
        self._rejects.clear()
        for match_id, flags in self._flags.items():
            since = self._flagged_at.setdefault(match_id, now)
            if flags and (final or now - since >= self.flag_every_s):
                refs = sorted({r.event_id for r in flags if r.event_id})
                raised.append(self.desk.raise_alert(
                    match_id, "validation_flag", "info",
                    f"{len(flags)} feed row(s) passed with a warning: {_summary(flags)}", refs,
                ))  # fmt: skip
                flags.clear()
                self._flagged_at[match_id] = now
        return [a for a in raised if a is not None]
