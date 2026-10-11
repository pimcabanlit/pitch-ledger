"""Ops alerts: the desk (ledger, store, push), audit-trail rules and feed rules."""

import json
from datetime import UTC, datetime
from pathlib import Path

from pitch_ledger.eventstore import FileEventStore
from pitch_ledger.ledger import FileLedger, InMemoryLedger
from pitch_ledger.ops import FeedWatch, OpsDesk, dead_lettered, review_insight
from pitch_ledger.push import RecordingNotifier
from pitch_ledger.schema import AuditRecord, Insight, InsightSubject, Trigger
from pitch_ledger.validation.results import violation

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _desk(ledger: InMemoryLedger | None = None, **kw: object) -> OpsDesk:
    return OpsDesk(ledger or InMemoryLedger(), now=lambda: NOW, **kw)  # type: ignore[arg-type]


def _insight(ledger: InMemoryLedger) -> Insight:
    insight = Insight(insight_id=ledger.next_id("M0002", "I"), match_id="M0002", period=2,
                      clock_ms=1_412_000, insight_type="momentum_shift",
                      subject=InsightSubject(team_id="T02"), headline="h", explanation="e",
                      metrics={"control_after": 6}, evidence_event_ids=["M0002-E000010"],
                      confidence=0.9, created_by_agent="explainer", model_version="offline",
                      trigger_id="M0002-T0040")  # fmt: skip
    ledger.put_insight(insight)
    return insight


def _audit(ledger: InMemoryLedger, insight: Insight, actor: str, action: str,
           findings: list[str]) -> None:  # fmt: skip
    ledger.add_audit(AuditRecord(audit_id=ledger.next_id("M0002", "A"), match_id="M0002",
                                 insight_id=insight.insight_id, round=1, actor=actor,
                                 action=action, findings=findings))  # type: ignore[arg-type]  # fmt: skip


def test_desk_records_in_the_ledger_store_and_push_and_acknowledges_once(tmp_path: Path) -> None:
    ledger, store = FileLedger(tmp_path / "ledger"), FileEventStore(tmp_path / "store")
    push = RecordingNotifier()
    desk = _desk(ledger, store=store, notifier=push)
    alert = desk.raise_alert("M0002", "feed_gap", "warning", "No feed", ["M0002-E000100"])
    assert alert is not None and alert.alert_id == "M0002-X0001" and alert.raised_at == NOW
    assert desk.raise_alert("M0002", "feed_gap", "warning", "again", ["M0002-E000100"]) is None
    acked = desk.acknowledge("M0002-X0001", "producer-oid-1")
    assert acked is not None and acked.acknowledged_by == "producer-oid-1"
    assert desk.acknowledge("M0002-X0001", "someone-else") == acked  # first one stays
    assert desk.acknowledge("M0002-X0099", "p") is None
    ledger.flush()
    store.close()
    assert FileLedger(tmp_path / "ledger").alerts("M0002") == [acked]  # survives a reload
    rows = [
        json.loads(x) for x in (tmp_path / "store" / "OpsAlerts.jsonl").read_text().splitlines()
    ]
    assert [r["acknowledged_by"] for r in rows] == [None, "producer-oid-1"]  # store appends
    signals = [(g, m["event"], m["acknowledged"]) for g, m in push.sent]
    assert signals == [("producers.M0002", "alert_changed", False),
                       ("producers.M0002", "alert_changed", True)]  # fmt: skip


def test_review_insight_reads_blocks_and_fallbacks_off_the_audit_trail() -> None:
    ledger = InMemoryLedger()
    desk = _desk(ledger)
    insight = _insight(ledger)
    _audit(ledger, insight, "explainer", "draft", [])
    _audit(ledger, insight, "auditor", "block", ["control_after: 61 does not recompute (6)"])
    _audit(ledger, insight, "explainer", "fallback", ["model call budget spent"])
    blocked, fallback = review_insight(desk, insight.insight_id)
    assert blocked.kind == "insight_blocked" and blocked.refs == [insight.insight_id]
    assert "at 68'" in blocked.message and "does not recompute" in blocked.message
    assert fallback.kind == "model_fallback" and fallback.severity == "warning"
    assert fallback.message.startswith("Model call budget spent")
    assert review_insight(desk, insight.insight_id) == []  # nothing new
    _audit(ledger, insight, "copy_check", "fallback", ["casual/es: numbers not in metrics: 7"])
    [later] = review_insight(desk, insight.insight_id)  # e.g. after the producer approved
    assert later.severity == "info" and later.refs == [insight.insight_id, "M0002-A0004"]
    assert review_insight(desk, "M0002-I0099") == [] and review_insight(desk, None) == []


def test_a_content_filter_fallback_is_a_named_warning() -> None:
    ledger = InMemoryLedger()
    desk = _desk(ledger)
    insight = _insight(ledger)
    _audit(ledger, insight, "explainer", "fallback", ["explainer: blocked by the content filter"])
    [alert] = review_insight(desk, insight.insight_id)
    assert alert.severity == "warning" and alert.message.startswith("Content filter: ")


def test_dead_lettered_trigger_is_critical() -> None:
    trigger = Trigger(trigger_id="M0002-T0007", match_id="M0002", period=1, clock_ms=600_000,
                      insight_type="key_moment", rule_id="goal",
                      subject=InsightSubject(team_id="T01"), metrics={},
                      evidence_event_ids=[])  # fmt: skip
    alert = dead_lettered(_desk(), trigger, 3, ["TimeoutError: model"])
    assert alert is not None and alert.severity == "critical"
    assert "goal at 10'" in alert.message and alert.message.endswith("TimeoutError: model")


def test_feed_watch_gap_once_per_gap_and_only_during_play() -> None:
    desk = _desk()
    watch = FeedWatch(desk, gap_s=10)
    watch.seen("M0002", 100.0)
    assert watch.check_gap("M0002", 109.0, True, "34'", "M0002-E000500") is None
    assert watch.check_gap("M0002", 130.0, False, "45'", "M0002-E000500") is None  # half time
    gap = watch.check_gap("M0002", 112.0, True, "34'", "M0002-E000500")
    assert gap is not None and gap.message.startswith("No feed message for 12 s")
    assert watch.check_gap("M0002", 125.0, True, "34'", "M0002-E000500") is None  # same gap
    watch.seen("M0002", 126.0)
    assert watch.check_gap("M0002", 140.0, True, "35'", "M0002-E000501") is not None
    assert watch.check_gap("M0003", 999.0, True, "1'", None) is None  # never seen


def test_feed_watch_lag_and_validation_summaries() -> None:
    desk = _desk()
    watch = FeedWatch(desk, lag_p95_ms=5000, flag_every_s=60)
    rows = [{"match_id": "M0002", "window_start": "2026-10-10T12:01:00+00:00", "messages": 900,
             "p50_ms": 2300, "p95_ms": 7200, "max_ms": 9100},
            {"match_id": "M0002", "window_start": "2026-10-10T12:02:00+00:00", "messages": 900,
             "p50_ms": 300, "p95_ms": 800, "max_ms": 1200}]  # fmt: skip
    [lag] = watch.check_lag(rows)
    assert "p95 7.2 s (max 9.1 s)" in lag.message and "12:01 UTC" in lag.message
    watch.validated("M0002", [violation("ROW-02", "M0002-E000123", "start_x 140 off pitch")])
    watch.validated("M0002", [violation("ROW-04", "M0002-E000130", "shot 150 km/h")])
    [reject] = watch.flush(now=0.0)
    assert reject.kind == "validation_reject" and reject.refs == ["M0002-E000123"]
    assert "ROW-02 x1" in reject.message
    assert watch.flush(now=30.0) == []  # flags wait for the minute
    [flag] = watch.flush(now=61.0)
    assert flag.kind == "validation_flag" and flag.severity == "info"
    assert watch.flush(now=200.0) == []
