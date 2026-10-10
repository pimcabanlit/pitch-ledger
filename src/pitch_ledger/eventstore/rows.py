"""Rows for the event store, built from the engine, detector and ledger (numbers from code).

``export_match`` writes a whole match directory: raw events and tracking, the engine's
team state every minute over its rolling 5-minute window, the detector's triggers and,
after a replay, each insight's status and the ops alerts. ``FeedHealthMeter`` turns feed
deliveries into per-minute latency rows.
"""

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pitch_ledger.agents.flow import airs
from pitch_ledger.engine.detector import detect
from pitch_ledger.engine.windows import MatchEngine, TeamWindow, match_minute
from pitch_ledger.eventstore.port import EventStore
from pitch_ledger.eventstore.tables import project
from pitch_ledger.matchlab.io import TRIGGERS_JSONL, read_match, read_triggers
from pitch_ledger.schema import Event, Insight, Match, OpsAlert, TrackingSample, Trigger

WINDOW_MS = 5 * 60_000
STEP_MS = 60_000
CHUNK = 5000


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def team_state_row(match_id: str, w: TeamWindow) -> dict[str, Any]:
    return {
        "match_id": match_id, "team_id": w.team_id, "period": w.period,
        "start_ms": w.start_ms, "end_ms": w.end_ms, "minute": round(w.end_minute, 2),
        "possession_share": w.possession_share, "possessions": w.possessions,
        "passes": w.passes, "passes_completed": w.passes_completed,
        "pass_accuracy": w.pass_accuracy, "passes_per_possession": w.passes_per_possession,
        "turnovers": w.turnovers, "own_half_turnovers": w.own_half_turnovers,
        "ppda": w.ppda, "tempo": w.tempo, "control": w.control,
    }  # fmt: skip


def team_state_rows(match: Match, events: list[Event]) -> list[dict[str, Any]]:
    """Each team's state at every whole minute of each period, over the trailing window."""
    engine = MatchEngine(match, events)
    rows = []
    for period in sorted({e.period for e in events}):
        end = engine.period_end_ms(period) or max(e.clock_ms for e in events if e.period == period)
        for end_ms in range(STEP_MS, end + 1, STEP_MS):
            for team in engine.teams:
                window = engine.team_window(team, period, max(0, end_ms - WINDOW_MS), end_ms)
                rows.append(team_state_row(match.match_id, window))
    return rows


def trigger_row(t: Trigger) -> dict[str, Any]:
    return {
        "trigger_id": t.trigger_id, "match_id": t.match_id, "period": t.period,
        "clock_ms": t.clock_ms, "minute": round(match_minute(t.period, t.clock_ms), 2),
        "insight_type": t.insight_type.value, "rule_id": t.rule_id,
        "team_id": t.subject.team_id, "player_id": t.subject.player_id,
        "metrics": t.metrics, "evidence_event_ids": t.evidence_event_ids,
        "auto_approve": t.auto_approve,
    }  # fmt: skip


def insight_status(insight: Insight) -> str:
    if insight.audit_status.value == "blocked":
        return "blocked"
    return "aired" if airs(insight) else "pending"


def insight_status_row(insight: Insight, recorded_at: float) -> dict[str, Any]:
    i = insight
    return {
        "insight_id": i.insight_id, "match_id": i.match_id, "trigger_id": i.trigger_id,
        "period": i.period, "clock_ms": i.clock_ms,
        "minute": round(match_minute(i.period, i.clock_ms), 2),
        "insight_type": i.insight_type.value, "team_id": i.subject.team_id,
        "player_id": i.subject.player_id, "headline": i.headline,
        "audit_status": i.audit_status.value, "status": insight_status(i),
        "approved_by": i.approved_by, "created_by_agent": i.created_by_agent,
        "model_version": i.model_version, "recorded_at": _utc(recorded_at),
    }  # fmt: skip


def _chunks(rows: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), CHUNK):
        yield rows[start : start + CHUNK]


def _ledger_lines(match_dir: Path, name: str) -> list[str]:
    path = match_dir / "ledger" / f"{name}.jsonl"
    return [x for x in path.read_text("utf-8").splitlines() if x] if path.exists() else []


def export_match(
    match_dir: Path, store: EventStore, with_tracking: bool = True, recorded_at: float = 0.0
) -> dict[str, int]:
    """Write a match directory to the store; returns rows written per table."""
    files = read_match(match_dir, with_tracking=with_tracking)
    events = [Event.model_validate(e) for e in files.events]
    if (match_dir / TRIGGERS_JSONL).exists():
        triggers = read_triggers(match_dir)
    else:
        tracking = [TrackingSample.model_validate(s) for s in files.tracking]
        triggers = detect(files.match, events, tracking)
    insights = [Insight.model_validate_json(x) for x in _ledger_lines(match_dir, "insights")]
    alerts = [OpsAlert.model_validate_json(x) for x in _ledger_lines(match_dir, "alerts")]
    recorded = recorded_at or datetime.now(UTC).timestamp()
    tables: dict[str, list[dict[str, Any]]] = {
        "Events": files.events,
        "Tracking": files.tracking,
        "TeamState": team_state_rows(files.match, events),
        "Triggers": [trigger_row(t) for t in triggers],
        "InsightStatus": [insight_status_row(i, recorded) for i in insights],
        "OpsAlerts": [project("OpsAlerts", a.model_dump(mode="json")) for a in alerts],
    }
    for table, rows in tables.items():
        for chunk in _chunks(rows):
            store.append(table, chunk)
    store.flush()
    return {table: len(rows) for table, rows in tables.items()}


@dataclass
class FeedHealthMeter:
    """Per-match, per-wall-clock-minute delivery counts and latency percentiles."""

    _buckets: dict[tuple[str, int], list[float]] = field(default_factory=lambda: defaultdict(list))
    _counts: dict[tuple[str, int], int] = field(default_factory=lambda: defaultdict(int))

    def add(self, match_id: str, received_at: float, latency_ms: float | None) -> None:
        key = (match_id, int(received_at // 60) * 60)
        self._counts[key] += 1
        if latency_ms is not None:
            self._buckets[key].append(latency_ms)

    def take(self, before: float = math.inf) -> list[dict[str, Any]]:
        """Rows for minutes that started before ``before`` (all by default); removes them."""
        rows = []
        for key in sorted(k for k in self._counts if k[1] + 60 <= before):
            values = sorted(self._buckets.pop(key, []))

            def pick(q: float, v: list[float] = values) -> float | None:
                return round(v[min(len(v) - 1, round(q * (len(v) - 1)))]) if v else None

            rows.append({
                "match_id": key[0], "window_start": _utc(key[1]),
                "messages": self._counts.pop(key), "p50_ms": pick(0.5), "p95_ms": pick(0.95),
                "max_ms": round(values[-1]) if values else None,
            })  # fmt: skip
        return rows
