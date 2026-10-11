"""Event-store tables: the single definition behind the KQL table script, the JSON
ingestion mappings and the row shapes every store adapter writes.

The event store is the analytical copy (Azure Data Explorer, or local files): the feed's
raw rows, the engine's metric snapshots, triggers, insight status, ops alerts, feed health,
and the season history (match results, team and player match stats, evaluation runs).
Cosmos DB stays the ledger's system of record. Columns follow
docs/event-schema.md; ``minute`` columns are match minutes computed in code (period 2
clock 15:00 is minute 60), so dashboard queries never derive football numbers.
"""

import json
from typing import Any

Column = tuple[str, str]  # (name, KQL type)

TABLES: dict[str, tuple[Column, ...]] = {
    "Events": (
        ("event_id", "string"), ("match_id", "string"), ("seq_no", "int"), ("period", "int"),
        ("clock_ms", "int"), ("feed_ts_utc", "datetime"), ("team_id", "string"),
        ("player_id", "string"), ("event_type", "string"), ("outcome", "string"),
        ("start_x", "real"), ("start_y", "real"), ("end_x", "real"), ("end_y", "real"),
        ("body_part", "string"), ("duration_ms", "int"), ("possession_id", "int"),
        ("possession_team_id", "string"), ("play_pattern", "string"),
        ("under_pressure", "bool"), ("nearest_opponent_m", "real"),
        ("receiver_player_id", "string"), ("related_event_ids", "dynamic"),
        ("score_home", "int"), ("score_away", "int"), ("players_on_pitch_home", "int"),
        ("players_on_pitch_away", "int"), ("details", "dynamic"),
    ),
    "Tracking": (
        ("match_id", "string"), ("period", "int"), ("clock_ms", "int"), ("player_id", "string"),
        ("x", "real"), ("y", "real"), ("speed_kmh", "real"),
    ),
    "TeamState": (
        ("match_id", "string"), ("team_id", "string"), ("period", "int"), ("start_ms", "int"),
        ("end_ms", "int"), ("minute", "real"), ("possession_share", "real"),
        ("possessions", "int"), ("passes", "int"), ("passes_completed", "int"),
        ("pass_accuracy", "real"), ("passes_per_possession", "real"), ("turnovers", "int"),
        ("own_half_turnovers", "int"), ("ppda", "real"), ("tempo", "real"), ("control", "real"),
    ),
    "Triggers": (
        ("trigger_id", "string"), ("match_id", "string"), ("period", "int"),
        ("clock_ms", "int"), ("minute", "real"), ("insight_type", "string"),
        ("rule_id", "string"), ("team_id", "string"), ("player_id", "string"),
        ("metrics", "dynamic"), ("evidence_event_ids", "dynamic"), ("auto_approve", "bool"),
    ),
    "InsightStatus": (
        ("insight_id", "string"), ("match_id", "string"), ("trigger_id", "string"),
        ("period", "int"), ("clock_ms", "int"), ("minute", "real"), ("insight_type", "string"),
        ("team_id", "string"), ("player_id", "string"), ("headline", "string"),
        ("audit_status", "string"), ("status", "string"), ("approved_by", "string"),
        ("created_by_agent", "string"), ("model_version", "string"),
        ("recorded_at", "datetime"),
    ),
    "OpsAlerts": (
        ("alert_id", "string"), ("match_id", "string"), ("kind", "string"),
        ("severity", "string"), ("message", "string"), ("refs", "dynamic"),
        ("raised_at", "datetime"), ("acknowledged_by", "string"),
    ),
    "FeedHealth": (
        ("match_id", "string"), ("window_start", "datetime"), ("messages", "long"),
        ("p50_ms", "real"), ("p95_ms", "real"), ("max_ms", "real"),
    ),
    "StoreCheck": (("check_id", "string"), ("written_at", "datetime")),
    # Season history: one row per match, per team and per player (append-only; a match
    # stored again adds identical rows, so queries take the latest per key).
    "MatchResults": (
        ("match_id", "string"), ("competition", "string"), ("season", "string"),
        ("kickoff_utc", "datetime"), ("home_team_id", "string"), ("away_team_id", "string"),
        ("score_home", "int"), ("score_away", "int"), ("hat_tricks", "dynamic"),
        ("record_version", "int"), ("recorded_at", "datetime"),
    ),
    "TeamMatchStats": (
        ("match_id", "string"), ("competition", "string"), ("season", "string"),
        ("kickoff_utc", "datetime"), ("team_id", "string"), ("opponent_id", "string"),
        ("venue", "string"), ("goals_for", "int"), ("goals_against", "int"),
        ("result", "string"), ("shots", "int"), ("on_target", "int"), ("xg", "real"),
        ("possession_pct", "real"), ("passes", "int"), ("pass_accuracy", "real"),
        ("recorded_at", "datetime"),
    ),
    "PlayerMatchStats": (
        ("match_id", "string"), ("competition", "string"), ("season", "string"),
        ("kickoff_utc", "datetime"), ("team_id", "string"), ("player_id", "string"),
        ("goals", "int"), ("shots", "int"), ("xg", "real"), ("passes", "int"),
        ("passes_completed", "int"), ("pass_accuracy", "real"), ("distance_km", "real"),
        ("top_speed_kmh", "real"), ("sprints", "int"), ("recorded_at", "datetime"),
    ),
    # Evaluation history: each scorecard (and Foundry evaluation) run with its gates.
    "EvalRuns": (
        ("run_id", "string"), ("kind", "string"), ("generated_at", "datetime"),
        ("passed", "bool"), ("matches", "dynamic"), ("gates", "dynamic"),
        ("summary", "dynamic"), ("recorded_at", "datetime"),
    ),
}  # fmt: skip


def mapping_name(table: str) -> str:
    return f"{table}_json"


def project(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """The row with exactly the table's columns (missing ones as null)."""
    return {name: row.get(name) for name, _ in TABLES[table]}


def create_script() -> str:
    """One KQL management script that creates every table, its JSON ingestion mapping and
    its ingestion policies. Run once in the cluster's query editor as a database admin;
    re-running is safe (create-merge / create-or-alter)."""
    lines = [
        "// Pitch Ledger event store: tables, JSON mappings, ingestion policies.",
        "// Generated by `pitch-ledger store-schema` from src/pitch_ledger/eventstore/tables.py.",
        ".execute database script with (ContinueOnErrors = true) <|",
    ]
    batching = json.dumps(
        {
            "MaximumBatchingTimeSpan": "00:00:10",
            "MaximumNumberOfItems": 500,
            "MaximumRawDataSizeMB": 100,
        }
    )
    for table, columns in TABLES.items():
        schema = ", ".join(f"['{name}']:{kind}" for name, kind in columns)
        mapping = json.dumps([{"column": n, "path": f"$.{n}"} for n, _ in columns])
        lines += [
            f".create-merge table ['{table}'] ({schema})",
            f".create-or-alter table ['{table}'] ingestion json mapping '{mapping_name(table)}'"
            f" '{mapping}'",
            f".alter table ['{table}'] policy streamingingestion enable",
            f".alter table ['{table}'] policy ingestionbatching '{batching}'",
        ]
    return "\n".join(lines) + "\n"
