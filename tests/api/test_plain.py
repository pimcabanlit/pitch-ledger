"""Plain words for the producer console: alert headlines and audit steps."""

from datetime import UTC, datetime

from pitch_ledger.api.plain import explain_alert, explain_step, reasons
from pitch_ledger.schema import AuditRecord, Insight, InsightSubject, OpsAlert, Trigger

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
INSIGHT = Insight(insight_id="M0002-I0017", match_id="M0002", period=2, clock_ms=50 * 60_000,
                  insight_type="momentum_shift", subject=InsightSubject(team_id="T02"),
                  headline="h", explanation="e", metrics={"control_after": 6},
                  evidence_event_ids=["M0002-E000010"], confidence=0.9,
                  created_by_agent="explainer", model_version="offline")  # fmt: skip


def _alert(
    kind: str, message: str, refs: list[str] | None = None, severity: str = "info"
) -> OpsAlert:
    return OpsAlert(alert_id="M0002-X0001", match_id="M0002", kind=kind, severity=severity,
                    message=message, refs=refs or [], raised_at=NOW)  # type: ignore[arg-type]  # fmt: skip


def _audit(actor: str, action: str, findings: list[str], passed: bool | None = None) -> AuditRecord:
    return AuditRecord(audit_id="M0002-A0079", match_id="M0002", insight_id="M0002-I0017",
                       round=0, actor=actor, action=action, passed=passed,
                       findings=findings)  # type: ignore[arg-type]  # fmt: skip


def test_a_copy_check_fallback_reads_as_one_plain_sentence() -> None:
    audit = _audit("copy_check", "fallback", [
        "casual/es: STYLE: casual copy has 3 figures (max 1); STYLE: cliché 'on fire'",
        "casual/fil: STYLE: casual copy has 3 figures (max 1)",
        "analyst/en: STYLE: no ordinals; write minutes like 77'",
    ])  # fmt: skip
    alert = _alert("model_fallback", "template copy used for M0002-I0017 at 95' (copy_check): ...",
                   ["M0002-I0017", "M0002-A0079"])  # fmt: skip
    brief = explain_alert(alert, INSIGHT, [audit])
    assert brief.headline == "3 versions of the 95' momentum shift aired with pre-written wording."
    assert not brief.needs_action and brief.action.startswith("No action needed")
    assert brief.details == [
        "Why: the AI's text had too many numbers, a cliché and minutes in the wrong format.",
        "Affected: Casual fan in Spanish and Filipino; Analyst in English.",
    ]
    assert "STYLE" not in explain_step(audit) and explain_step(audit).startswith("3 on-screen")


def test_budget_and_content_filter_fallbacks_say_why() -> None:
    budget = _audit("explainer", "fallback", ["model unavailable: model call budget spent"])
    alert = _alert("model_fallback", "Model call budget spent: ...", ["M0002-I0017", "M0002-A0079"])
    assert explain_alert(alert, INSIGHT, [budget]).headline.startswith(
        "The AI call budget is used up"
    )
    blocked = _audit("explainer", "fallback", ["explainer: blocked by the content filter"])
    assert "safety filter" in explain_alert(alert, INSIGHT, [blocked]).headline
    plain = _alert("model_fallback", "template copy used")  # no records to read
    assert explain_alert(plain).headline == "An insight aired with pre-written wording."


def test_blocks_dead_letters_and_feed_alerts() -> None:
    block = _audit("auditor", "block", ["control_after: 61 does not recompute (6)"], False)
    brief = explain_alert(_alert("insight_blocked", "Auditor blocked ..."), INSIGHT, [block])
    assert brief.headline == ("The Auditor stopped the 95' momentum shift because of a number "
                              "that does not match the match data. It will not air.")  # fmt: skip
    trigger = Trigger(trigger_id="M0002-T0007", match_id="M0002", period=1, clock_ms=600_000,
                      insight_type="key_moment", rule_id="goal",
                      subject=InsightSubject(team_id="T01"), metrics={},
                      evidence_event_ids=[])  # fmt: skip
    message = "Trigger M0002-T0007 (goal at 10') failed 3 delivery attempts"
    dead = explain_alert(
        _alert("trigger_dead_lettered", message, severity="critical"), trigger=trigger
    )
    assert (
        dead.headline
        == "No insight could be made for the 10' goal after 3 tries, so it was dropped."
    )
    assert dead.needs_action
    gap = explain_alert(_alert("feed_gap", "No feed message for 12 s during play (last at 34')."))
    assert gap.headline == "The live match data stopped arriving for 12 seconds at 34'."
    assert gap.needs_action
    lag = explain_alert(_alert("feed_lag", "Feed lag: p95 6.2 s (max 8.0 s) over 40 messages"))
    assert lag.headline == "The live match data is arriving about 6.2 seconds late."
    rejects = explain_alert(_alert("validation_reject", "1 feed row(s) rejected and dropped"))
    assert rejects.headline == "1 faulty data row from the live feed was dropped before analysis."
    flags = explain_alert(_alert("validation_flag", "5 feed row(s) passed with a warning: x"))
    assert flags.headline == "5 data rows from the live feed looked unusual but were kept."


def test_audit_steps_and_reasons_in_plain_words() -> None:
    assert explain_step(_audit("explainer", "draft", [])) == "The Explainer wrote a draft."
    assert explain_step(_audit("auditor", "check", [], True)).endswith("all correct.")
    failed = _audit(
        "auditor", "check", ["evidence M0002-E000999 is not part of this moment"], False
    )
    assert explain_step(failed) == "The Auditor found a problem: it cited the wrong match events."
    planted = _audit("auditor", "check", ["INS-01: '32' in the text matches no cited metric "
                                          "(cited: turnovers_after=16)"], False)  # fmt: skip
    assert explain_step(planted) == (
        "The Auditor found a problem: a number that does not match the match data."
    )
    assert explain_step(_audit("test", "inject_error", ["control 61"])).startswith("Demo test")
    story = _audit("story_check", "fallback", [
        "plain_outlook/es: STYLE: plain story beat has 3 figures (max 2)",
        "numbers_buildup/fil: numbers not in metrics: 7",
    ])  # fmt: skip
    assert explain_step(story) == ("2 story beats used pre-written wording (the AI's text had "
                                   "too many numbers and a number that does not match the "
                                   "match data).")  # fmt: skip
    alert = _alert("model_fallback", "template copy used", ["M0002-I0017", "M0002-A0079"])
    assert explain_alert(alert, INSIGHT, [story]).details[-1] == (
        "Affected: Fan story outlook in Spanish; Analyst story buildup in Filipino."
    )
    assert explain_step(_audit("p-1", "producer_block", ["off brand"])) == "A producer blocked it."
    assert reasons(["STYLE: cliché 'game of two halves'", "STYLE: weird new rule"]) == [
        "a cliché", "it broke the house style"]  # fmt: skip
