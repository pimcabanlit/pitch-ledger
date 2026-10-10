"""One passing and at least one failing case per validation rule."""

import copy
from typing import Any

import pytest

from pitch_ledger.schema import Insight, TrackingSample
from pitch_ledger.validation import RULES, check_insight, validate_match
from pitch_ledger.validation.realism import BANDS, RealismCheck, check_realism
from pitch_ledger.validation.results import ValidationReport
from tests.validation.factories import baseline_rows, find, make_match, make_players

Rows = list[dict[str, Any]]


def run(rows: Rows, tracking: list[dict[str, Any]] | None = None) -> ValidationReport:
    return validate_match(make_match(), make_players(), rows, tracking, realism=False)


@pytest.fixture
def rows() -> Rows:
    return copy.deepcopy(baseline_rows())


def assert_fails(rows: Rows, rule_id: str, tracking: list[dict[str, Any]] | None = None) -> None:
    report = run(rows, tracking)
    assert report.for_rule(rule_id), f"{rule_id} not raised; got {report.counts()}"
    assert all(r.action == RULES[rule_id].action for r in report.for_rule(rule_id))


def test_baseline_passes_every_event_rule(rows: Rows) -> None:
    report = run(rows)
    assert report.results == []
    assert report.ok
    assert report.events_checked == len(rows)


def test_rule_catalogue_matches_doc_table() -> None:
    assert len(RULES) == 12
    assert [r.action for r in RULES.values()].count("reject") == 8


# ROW-01 -------------------------------------------------------------------
@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: find(r, "pass").update(event_type="header"), id="bad-enum"),
        pytest.param(lambda r: find(r, "pass").pop("possession_id"), id="missing-field"),
        pytest.param(lambda r: find(r, "pass").update(outcome="won"), id="outcome-for-type"),
        pytest.param(lambda r: find(r, "period_start").update(outcome="success"), id="no-outcome"),
        pytest.param(lambda r: find(r, "carry").update(player_id=None), id="missing-actor"),
        pytest.param(lambda r: find(r, "out_of_play").update(team_id="T01"), id="no-actor-type"),
        pytest.param(lambda r: find(r, "card").update(start_x=1.0), id="coords-on-card"),
        pytest.param(lambda r: find(r, "tackle").update(end_y=None), id="missing-coords"),
        pytest.param(lambda r: find(r, "carry").update(event_id="M0001-E000999"), id="event-id"),
        pytest.param(
            lambda r: find(r, "carry").update(
                match_id="M0002", event_id=find(r, "carry")["event_id"].replace("M0001", "M0002")
            ),
            id="other-match",
        ),
        pytest.param(
            lambda r: find(r, "carry").update(receiver_player_id="T02-P09"),
            id="receiver-on-non-pass",
        ),
        pytest.param(lambda r: find(r, "card").update(details={}), id="details-shape"),
    ],
)
def test_row01_fails(rows: Rows, mutate: Any) -> None:
    mutate(rows)
    assert_fails(rows, "ROW-01")


# ROW-02 -------------------------------------------------------------------
@pytest.mark.parametrize(("field", "value"), [("start_x", 105.5), ("end_y", -0.1)])
def test_row02_fails_off_pitch(rows: Rows, field: str, value: float) -> None:
    find(rows, "carry")[field] = value
    assert_fails(rows, "ROW-02")


def test_row02_passes_on_lines(rows: Rows) -> None:
    find(rows, "carry").update(start_x=0.0, start_y=68.0, end_x=105.0, end_y=0.0)
    assert not run(rows).for_rule("ROW-02")


# ROW-03 -------------------------------------------------------------------
def test_row03_fails_wrong_team(rows: Rows) -> None:
    find(rows, "carry")["player_id"] = "T01-P05"
    assert_fails(rows, "ROW-03")


def test_row03_fails_not_in_squad(rows: Rows) -> None:
    find(rows, "carry")["player_id"] = "T02-P30"
    assert_fails(rows, "ROW-03")


def test_row03_fails_subbed_off_player_acts(rows: Rows) -> None:
    find(rows, "carry")["player_id"] = "T02-P11"
    assert_fails(rows, "ROW-03")


def test_row03_fails_bench_player_acts(rows: Rows) -> None:
    find(rows, "pressure")["player_id"] = "T01-P12"
    assert_fails(rows, "ROW-03")


def test_row03_fails_sent_off_player_acts(rows: Rows) -> None:
    find(rows, "card").update(outcome="red")
    find(rows, "interception", 0)["player_id"] = "T01-P04"
    assert_fails(rows, "ROW-03")


def test_row03_fails_sub_in_already_played(rows: Rows) -> None:
    find(rows, "substitution")["details"]["player_in_id"] = "T02-P05"
    assert_fails(rows, "ROW-03")


def test_row03_passes_sub_on_then_acts(rows: Rows) -> None:
    assert find(rows, "tackle")["player_id"] == "T02-P12"
    assert not run(rows).for_rule("ROW-03")


# ROW-04 -------------------------------------------------------------------
@pytest.mark.parametrize("speed", [29.9, 140.1])
def test_row04_flags_shot_speed(rows: Rows, speed: float) -> None:
    find(rows, "shot", 1)["details"]["shot_speed_kmh"] = speed
    assert_fails(rows, "ROW-04")


def test_row04_flags_ball_speed(rows: Rows) -> None:
    find(rows, "pass").update(start_x=10.0, end_x=60.0, duration_ms=1000)  # 50 m in 1 s
    assert_fails(rows, "ROW-04")


def tracking_row(speed: float) -> dict[str, Any]:
    return {"match_id": "M0001", "period": 1, "clock_ms": 1000, "player_id": "T01-P05",
            "x": 30.0, "y": 20.0, "speed_kmh": speed}  # fmt: skip


def test_row04_flags_tracking_speed(rows: Rows) -> None:
    assert_fails(rows, "ROW-04", [tracking_row(37.0)])


def test_row04_passes_plausible_speeds(rows: Rows) -> None:
    find(rows, "shot", 1)["details"]["shot_speed_kmh"] = 139.0
    assert not run(rows, [tracking_row(36.9)]).for_rule("ROW-04")


def test_tracking_rows_checked_for_bounds_and_shape(rows: Rows) -> None:
    assert_fails(rows, "ROW-02", [tracking_row(10.0) | {"x": 106.0}])
    assert_fails(rows, "ROW-01", [tracking_row(10.0) | {"clock_ms": 1500}])
    sample = TrackingSample.model_validate(tracking_row(10.0))
    assert run(rows, [sample.model_dump()]).results == []


# SEQ-01 -------------------------------------------------------------------
def test_seq01_fails_gap(rows: Rows) -> None:
    del rows[5]
    assert_fails(rows, "SEQ-01")


def test_seq01_fails_clock_goes_back(rows: Rows) -> None:
    find(rows, "carry")["clock_ms"] = 0
    assert_fails(rows, "SEQ-01")


def test_seq01_passes_clock_reset_at_new_period(rows: Rows) -> None:
    assert find(rows, "period_start", 1)["clock_ms"] == 0
    assert not run(rows).for_rule("SEQ-01")


# SEQ-02 -------------------------------------------------------------------
def test_seq02_flags_receipt_by_other_player(rows: Rows) -> None:
    find(rows, "ball_receipt")["player_id"] = "T01-P07"
    assert_fails(rows, "SEQ-02")


def test_seq02_flags_missing_receipt(rows: Rows) -> None:
    receipt = find(rows, "ball_receipt", 1)
    receipt.update(event_type="carry", duration_ms=500)
    assert_fails(rows, "SEQ-02")


def test_seq02_passes_incomplete_pass_without_receipt(rows: Rows) -> None:
    assert find(rows, "pass", 3)["outcome"] == "incomplete"
    assert not run(rows).for_rule("SEQ-02")


# SEQ-03 -------------------------------------------------------------------
def test_seq03_fails_id_change_without_team_change(rows: Rows) -> None:
    for row in rows[rows.index(find(rows, "carry")) :]:
        row["possession_id"] += 1
    assert_fails(rows, "SEQ-03")


def test_seq03_fails_team_change_without_id_change(rows: Rows) -> None:
    tackle = find(rows, "tackle")
    tackle["possession_id"] -= 1
    assert_fails(rows, "SEQ-03")


# SEQ-04 -------------------------------------------------------------------
def test_seq04_fails_score_not_incremented(rows: Rows) -> None:
    after_goal = rows.index(find(rows, "shot")) + 1
    for row in rows[after_goal:]:
        row["score_home"] = 0
    assert_fails(rows, "SEQ-04")


def test_seq04_fails_no_kickoff_after_goal(rows: Rows) -> None:
    kickoff = rows[rows.index(find(rows, "shot")) + 1]
    kickoff["details"]["set_piece"] = "none"
    assert_fails(rows, "SEQ-04")


def test_seq04_fails_score_change_without_goal(rows: Rows) -> None:
    for row in rows[rows.index(find(rows, "carry")) :]:
        row["score_away"] = 1
    assert_fails(rows, "SEQ-04")


def test_seq04_passes_with_admin_events_before_kickoff(rows: Rows) -> None:
    assert not run(rows).for_rule("SEQ-04")


# SEQ-05 -------------------------------------------------------------------
def test_seq05_fails_forward_reference(rows: Rows) -> None:
    find(rows, "carry")["related_event_ids"] = ["M0001-E000999"]
    assert_fails(rows, "SEQ-05")


def test_seq05_fails_assist_not_seen(rows: Rows) -> None:
    find(rows, "shot")["details"]["assist_event_id"] = "M0001-E000500"
    assert_fails(rows, "SEQ-05")


# MATCH-01 -----------------------------------------------------------------
def test_match01_fails_missing_period_end(rows: Rows) -> None:
    rows.pop()
    assert_fails(rows, "MATCH-01")


def test_match01_fails_duplicate_period_start(rows: Rows) -> None:
    find(rows, "carry").update(
        event_type="period_start", team_id=None, player_id=None, outcome=None,
        start_x=None, start_y=None, end_x=None, end_y=None, duration_ms=None,
    )  # fmt: skip
    assert_fails(rows, "MATCH-01")


# MATCH-02 -----------------------------------------------------------------
def test_match02_flags_out_of_band() -> None:
    report = validate_match(make_match(), make_players(), baseline_rows(), [], realism=True)
    flagged = {r.message.split()[0] for r in report.for_rule("MATCH-02")}
    assert {"passes_both_teams", "shots_both_teams", "tackles_both_teams"} <= flagged
    assert report.ok  # flags never reject


def test_match02_passes_in_band() -> None:
    checks = [
        RealismCheck(BANDS["passes_both_teams"], (880,)),
        RealismCheck(BANDS["top_speed_kmh"], (29.0, 33.5)),
    ]
    assert check_realism(checks) == []
    assert check_realism([RealismCheck(BANDS["top_speed_kmh"], (29.0, 37.0))])


# INS-01 -------------------------------------------------------------------
def insight(**changes: Any) -> Insight:
    base = {
        "insight_id": "M0001-I0031", "match_id": "M0001", "period": 2, "clock_ms": 912000,
        "insight_type": "momentum_shift", "subject": {"team_id": "T02"},
        "headline": "Away side's build-up has broken down",
        "explanation": "4 own-half turnovers in 6 minutes since the home side switched to a "
                       "high press; control 64 → 31, PPDA 6.2",
        "metrics": {"own_half_turnovers": 4, "window_min": 6, "control_before": 64,
                    "control_after": 31, "ppda": 6.2},
        "evidence_event_ids": ["M0001-E000598"], "confidence": 0.86,
        "created_by_agent": "explainer", "model_version": "v0.1",
    }  # fmt: skip
    return Insight.model_validate(base | changes)


def test_ins01_passes_doc_example() -> None:
    assert check_insight(insight()) == []


def test_ins01_blocks_number_missing_from_metrics() -> None:
    results = check_insight(insight(headline="T02 lose the ball 7 times"))
    assert [r.rule_id for r in results] == ["INS-01"]
    assert results[0].action == "block"
    assert "7" in results[0].message


def test_ins01_respects_display_precision() -> None:
    assert check_insight(insight(explanation="PPDA 6.2, control 64", metrics={"ppda": 6.24,
                                 "control": 63.6})) == []  # fmt: skip
    assert check_insight(insight(explanation="PPDA 6.3", metrics={"ppda": 6.2}))


@pytest.mark.parametrize(("written", "ok"), [("31'", True), ("30'", True), ("32'", False)])
def test_ins01_lets_an_exact_half_round_either_way(written: str, ok: bool) -> None:
    """Python rounds 30.5 to 30 (half to even); a broadcast caption writes 31'."""
    results = check_insight(insight(headline=f"Goal at {written}", explanation="",
                                    metrics={"minute": 30.5}))  # fmt: skip
    assert (results == []) is ok


def test_ins01_reads_thousands_and_ignores_ids() -> None:
    ok = insight(headline="T02 complete 1,050 passes", explanation="", metrics={"passes": 1050})
    assert check_insight(ok) == []


def test_ins01_recompute() -> None:
    good = insight()
    assert check_insight(good, recompute=lambda i: dict(i.metrics)) == []
    wrong = check_insight(good, recompute=lambda i: dict(i.metrics) | {"ppda": 7.9})
    assert wrong and "ppda" in wrong[0].message
    missing = check_insight(good, recompute=lambda i: {})
    assert len(missing) == len(good.metrics)
