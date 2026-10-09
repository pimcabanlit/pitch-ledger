"""JSON round-trip and contract tests for the schema models."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from pitch_ledger.schema import (
    Event,
    Insight,
    Match,
    NoDetails,
    Overlay,
    PassDetails,
    Player,
    Scenario,
    ShotDetails,
    Team,
    TrackingSample,
)
from pitch_ledger.schema.enums import ALLOWED_OUTCOMES, EventType

KICKOFF = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)

# Worked example from docs/event-schema.md (period 2, home T01 vs away T02, 0-0).
WORKED_EXAMPLE: list[dict[str, Any]] = [
    dict(seq_no=598, clock_ms=803000, team_id="T02", player_id="T02-P05", event_type="pass",
         outcome="complete", start_x=30.0, start_y=20.0, end_x=45.0, end_y=28.0, duration_ms=900,
         possession_id=57, possession_team_id="T02", under_pressure=False,
         receiver_player_id="T02-P06",
         details={"pass_type": "short", "height": "ground", "set_piece": "none"}),
    dict(seq_no=599, clock_ms=803900, team_id="T02", player_id="T02-P06",
         event_type="ball_receipt", outcome="success", start_x=45.0, start_y=28.0, end_x=45.0,
         end_y=28.0, possession_id=57, possession_team_id="T02", under_pressure=True,
         related_event_ids=["M0001-E000598"]),
    dict(seq_no=600, clock_ms=804300, team_id="T01", player_id="T01-P09", event_type="pressure",
         outcome="forced_turnover", start_x=60.0, start_y=40.0, end_x=60.0, end_y=40.0,
         duration_ms=1100, possession_id=57, possession_team_id="T02", under_pressure=False,
         related_event_ids=["M0001-E000599"],
         details={"target_player_id": "T02-P06", "counterpress": False}),
    dict(seq_no=601, clock_ms=805400, team_id="T01", player_id="T01-P09", event_type="tackle",
         outcome="won", start_x=60.0, start_y=40.0, end_x=60.0, end_y=40.0, possession_id=58,
         possession_team_id="T01", play_pattern="from_counter",
         related_event_ids=["M0001-E000600"], details={"opponent_player_id": "T02-P06"}),
    dict(seq_no=602, clock_ms=805900, team_id="T01", player_id="T01-P09", event_type="carry",
         outcome="success", start_x=60.0, start_y=40.0, end_x=78.0, end_y=36.0,
         duration_ms=3100, possession_id=58, possession_team_id="T01",
         play_pattern="from_counter", related_event_ids=["M0001-E000601"]),
    dict(seq_no=603, clock_ms=809000, team_id="T01", player_id="T01-P09", event_type="pass",
         outcome="complete", start_x=78.0, start_y=36.0, end_x=93.0, end_y=33.0, duration_ms=800,
         possession_id=58, possession_team_id="T01", play_pattern="from_counter",
         under_pressure=True, receiver_player_id="T01-P10",
         details={"pass_type": "through_ball", "height": "ground", "set_piece": "none"}),
    dict(seq_no=604, clock_ms=810200, team_id="T01", player_id="T01-P10", event_type="shot",
         outcome="goal", start_x=94.0, start_y=33.0, end_x=105.0, end_y=34.5, duration_ms=350,
         possession_id=58, possession_team_id="T01", play_pattern="from_counter",
         related_event_ids=["M0001-E000603"],
         details={"shot_type": "open_play", "shot_speed_kmh": 96.4, "on_target": True,
                  "goalkeeper_id": "T02-P01", "assist_event_id": "M0001-E000603",
                  "assist_type": "through_ball"}),
]  # fmt: skip


def worked_event(row: dict[str, Any]) -> dict[str, Any]:
    """Fill the columns the CSV extract leaves out."""
    seq = row["seq_no"]
    base: dict[str, Any] = {
        "event_id": f"M0001-E{seq:06d}",
        "match_id": "M0001",
        "period": 2,
        "feed_ts_utc": "2026-10-24T15:16:03.400Z",
        "play_pattern": "regular_play",
        "score_home": 0,
        "score_away": 0,
        "players_on_pitch_home": 11,
        "players_on_pitch_away": 11,
    }
    return {**base, **row}


def assert_round_trip(model: BaseModel) -> None:
    text = model.model_dump_json()
    again = type(model).model_validate_json(text)
    assert again == model
    assert again.model_dump_json() == text


@pytest.mark.parametrize("row", WORKED_EXAMPLE, ids=lambda r: str(r["seq_no"]))
def test_worked_example_events_round_trip(row: dict[str, Any]) -> None:
    event = Event.model_validate(worked_event(row))
    assert_round_trip(event)


def test_details_are_typed_by_event_type() -> None:
    events = [Event.model_validate(worked_event(r)) for r in WORKED_EXAMPLE]
    assert isinstance(events[0].details, PassDetails)
    assert isinstance(events[1].details, NoDetails)
    shot = events[-1].details
    assert isinstance(shot, ShotDetails)
    assert shot.assist_type == "through_ball"


def test_wrong_details_for_type_rejected() -> None:
    row = worked_event(WORKED_EXAMPLE[1]) | {"details": {"pass_type": "short"}}
    with pytest.raises(ValidationError):
        Event.model_validate(row)


def test_unknown_enum_rejected() -> None:
    with pytest.raises(ValidationError):
        Event.model_validate(worked_event(WORKED_EXAMPLE[0]) | {"event_type": "header"})


def test_timestamp_serialised_with_milliseconds() -> None:
    event = Event.model_validate(worked_event(WORKED_EXAMPLE[0]))
    assert event.model_dump(mode="json")["feed_ts_utc"] == "2026-10-24T15:16:03.400Z"


def test_every_event_type_has_outcome_rule() -> None:
    assert set(ALLOWED_OUTCOMES) == set(EventType)
    assert len(EventType) == 18


def test_reference_tables_round_trip() -> None:
    assert_round_trip(
        Match(
            match_id="M0001", competition="Harbour Coast League", season="2026-27",
            kickoff_utc=KICKOFF, venue="Harbourside Park", home_team_id="T01",
            away_team_id="T02", referee="A. Navarro", scenario_id="none",
            generator_version="0.1.0", random_seed=4211,
        )
    )  # fmt: skip
    assert_round_trip(
        Team(team_id="T01", name="Northbridge Athletic", short_name="NBR",
             primary_color="#1B4F9C", formation="4-3-3", style_profile="high_press")
    )  # fmt: skip
    assert_round_trip(
        Player.model_validate(
            {"player_id": "T01-P08", "team_id": "T01", "name": "Rafael Santos",
             "display_name": "R. Santos", "shirt_number": 8, "position": "cm",
             "preferred_foot": "right", "is_starter": True, "top_speed_kmh": 33.1,
             "ratings": {"pace": 78, "passing": 84, "finishing": 61, "pressing": 80}}
        )
    )  # fmt: skip


def test_rating_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        Player.model_validate(
            {"player_id": "T01-P08", "team_id": "T01", "name": "X", "display_name": "X",
             "shirt_number": 8, "position": "cm", "preferred_foot": "right",
             "is_starter": True, "top_speed_kmh": 33.1,
             "ratings": {"pace": 100, "passing": 84, "finishing": 61, "pressing": 80}}
        )  # fmt: skip


def test_tracking_round_trip() -> None:
    sample = TrackingSample(
        match_id="M0001", period=2, clock_ms=783000, player_id="T01-P08", x=42.7, y=31.0,
        speed_kmh=26.4,
    )  # fmt: skip
    assert_round_trip(sample)
    with pytest.raises(ValidationError):
        TrackingSample.model_validate(sample.model_dump() | {"clock_ms": 783500})


def test_insight_round_trip_keeps_int_metrics() -> None:
    insight = Insight.model_validate(
        {"insight_id": "M0001-I0031", "match_id": "M0001", "period": 2, "clock_ms": 912000,
         "insight_type": "momentum_shift", "subject": {"team_id": "T02"},
         "headline": "Away side's build-up has broken down",
         "explanation": "4 own-half turnovers in 6 minutes since the home side switched "
                        "to a high press",
         "metrics": {"own_half_turnovers": 4, "window_min": 6, "control_before": 64,
                     "control_after": 31, "ppda": 6.2},
         "evidence_event_ids": ["M0001-E000598"], "confidence": 0.86,
         "created_by_agent": "explainer", "model_version": "v0.1"}
    )  # fmt: skip
    assert_round_trip(insight)
    assert '"control_before":64,' in insight.model_dump_json()


def test_overlay_doc_example_round_trip() -> None:
    overlay = Overlay.model_validate_json(
        """{"overlay_id": "M0001-O0112", "insight_id": "M0001-I0031", "period": 2,
        "trigger_clock_ms": 912000, "display_ms": 8000, "priority": 2,
        "template": "momentum_bar", "audience_mode": "analyst", "language": "en",
        "text": "Control flipped: away side 64 → 31 after the home press (PPDA 6.2)",
        "data": {"team_id": "T02", "control": [64, 31], "ppda": 6.2}}"""
    )
    assert_round_trip(overlay)
    with pytest.raises(ValidationError):
        Overlay.model_validate(overlay.model_dump() | {"language": "English"})


def test_scenario_round_trip_and_rules() -> None:
    scenario = Scenario.model_validate(
        {"scenario_id": "late_collapse",
         "description": "Away side tires and loses control after 60'",
         "trigger": {"period": 2, "minute": 60}, "team_id": "T02",
         "effects": {"pass_success": {"op": "add", "value": -0.08},
                     "turnover_rate": {"op": "mul", "value": 1.6},
                     "top_speed": {"op": "mul", "value": 0.92}},
         "expected_detection": {"insight_type": "momentum_shift", "team_id": "T02",
                                "within_min": 6}}
    )  # fmt: skip
    assert_round_trip(scenario)
    assert_round_trip(Scenario(scenario_id="none", description="Baseline"))
    with pytest.raises(ValidationError):
        Scenario(scenario_id="red_card", description="no trigger", team_id="T02")
