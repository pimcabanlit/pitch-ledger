"""Ledger (memory and file) and the trigger queue."""

from pathlib import Path

from pitch_ledger.agents.queue import InMemoryTriggerQueue
from pitch_ledger.ledger import FileLedger, InMemoryLedger
from pitch_ledger.schema import AuditRecord, Insight, InsightSubject, Overlay, Trigger


def _trigger(n: int = 1) -> Trigger:
    return Trigger(trigger_id=f"M0002-T{n:04d}", match_id="M0002", period=2, clock_ms=1_000_000,
                   insight_type="momentum_shift", rule_id="momentum_shift",
                   subject=InsightSubject(team_id="T02"), metrics={"control_after": 6},
                   evidence_event_ids=["M0002-E000010"])  # fmt: skip


def _insight(ledger: InMemoryLedger) -> Insight:
    return Insight(insight_id=ledger.next_id("M0002", "I"), match_id="M0002", period=2,
                   clock_ms=1_000_000, insight_type="momentum_shift",
                   subject=InsightSubject(team_id="T02"), headline="h", explanation="Control 6.",
                   metrics={"control_after": 6}, evidence_event_ids=["M0002-E000010"],
                   confidence=0.9, created_by_agent="explainer", model_version="offline",
                   trigger_id="M0002-T0001")  # fmt: skip


def _fill(ledger: InMemoryLedger) -> Insight:
    ledger.save_trigger(_trigger())
    insight = _insight(ledger)
    ledger.put_insight(insight)
    ledger.add_audit(AuditRecord(audit_id=ledger.next_id("M0002", "A"), match_id="M0002",
                                 insight_id=insight.insight_id, round=1, actor="auditor",
                                 action="approve", passed=True))  # fmt: skip
    ledger.put_overlay(Overlay(overlay_id=ledger.next_id("M0002", "O"),
                               insight_id=insight.insight_id, period=2,
                               trigger_clock_ms=1_000_000, display_ms=8000, priority=1,
                               template="momentum_bar", audience_mode="analyst", language="en",
                               text="Control 6", data={"control": 6}))  # fmt: skip
    return insight


def test_ids_are_sequential_per_match_and_kind() -> None:
    ledger = InMemoryLedger()
    assert [ledger.next_id("M0002", "I") for _ in range(2)] == ["M0002-I0001", "M0002-I0002"]
    assert ledger.next_id("M0003", "I") == "M0003-I0001"
    assert ledger.next_id("M0002", "O") == "M0002-O0001"


def test_memory_ledger_round_trip() -> None:
    ledger = InMemoryLedger()
    insight = _fill(ledger)
    assert ledger.get_insight(insight.insight_id) == insight
    assert [a.action for a in ledger.audits(insight.insight_id)] == ["approve"]
    assert len(ledger.overlays(insight.insight_id)) == 1
    updated = insight.model_copy(
        update={"audit_status": "approved", "approved_by": "producer_demo"}
    )
    ledger.put_insight(updated)
    assert ledger.insights("M0002") == [updated]


def test_file_ledger_persists_and_resumes_ids(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path / "ledger")
    insight = _fill(ledger)
    paths = ledger.flush()
    assert {p.name for p in paths} == {"triggers.jsonl", "insights.jsonl", "audits.jsonl",
                                       "overlays.jsonl", "alerts.jsonl"}  # fmt: skip
    again = FileLedger(tmp_path / "ledger")
    assert again.get_insight(insight.insight_id) == insight
    assert again.triggers["M0002-T0001"] == _trigger()
    assert again.next_id("M0002", "I") == "M0002-I0002"
    assert again.next_id("M0002", "A") == "M0002-A0002"
    assert again.get_trigger("M0002-T0001") == _trigger()
    fresh = FileLedger(tmp_path / "ledger", fresh=True)
    assert fresh.insights() == [] and fresh.next_id("M0002", "I") == "M0002-I0001"


def test_queue_retries_then_dead_letters() -> None:
    queue = InMemoryTriggerQueue(max_deliveries=3)
    queue.send(_trigger(1))
    queue.send(_trigger(2))
    first = queue.receive()
    assert first is not None and first.delivery_count == 1
    queue.complete(first)
    for attempt in range(1, 4):
        d = queue.receive()
        assert d is not None and d.trigger.trigger_id == "M0002-T0002"
        assert d.delivery_count == attempt
        queue.abandon(d, f"boom {attempt}")
    assert queue.receive() is None
    dead = queue.dead_letters()
    assert [d.trigger.trigger_id for d in dead] == ["M0002-T0002"]
    assert dead[0].errors == ["boom 1", "boom 2", "boom 3"]
    assert [d.message_id for d in queue.completed] == ["M0002-T0001"]
