"""Read and write a generated match directory.

Layout of ``data/<match_id>/``:

- ``match.json``: the match row, both team rows and the match-day player rows
- ``events.jsonl`` / ``tracking.jsonl``: one JSON object per line (live-feed format)
- ``events.parquet`` / ``tracking.parquet``: the same rows as columnar tables; the
  heterogeneous ``details`` object is stored as a JSON string column
- ``triggers.jsonl``: detector output, written by ``pitch-ledger detect``

Writers are deterministic: the same rows always produce the same bytes.
"""

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from pitch_ledger.schema import Event, Match, Player, Team, TrackingSample, Trigger

MATCH_FILE = "match.json"
EVENTS_JSONL = "events.jsonl"
EVENTS_PARQUET = "events.parquet"
TRACKING_JSONL = "tracking.jsonl"
TRACKING_PARQUET = "tracking.parquet"
TRIGGERS_JSONL = "triggers.jsonl"

_EVENT_SCHEMA = pa.schema(
    [
        ("event_id", pa.string()),
        ("match_id", pa.string()),
        ("seq_no", pa.int32()),
        ("period", pa.int8()),
        ("clock_ms", pa.int32()),
        ("feed_ts_utc", pa.timestamp("ms", tz="UTC")),
        ("team_id", pa.string()),
        ("player_id", pa.string()),
        ("event_type", pa.string()),
        ("outcome", pa.string()),
        ("start_x", pa.float64()),
        ("start_y", pa.float64()),
        ("end_x", pa.float64()),
        ("end_y", pa.float64()),
        ("body_part", pa.string()),
        ("duration_ms", pa.int32()),
        ("possession_id", pa.int32()),
        ("possession_team_id", pa.string()),
        ("play_pattern", pa.string()),
        ("under_pressure", pa.bool_()),
        ("nearest_opponent_m", pa.float64()),
        ("receiver_player_id", pa.string()),
        ("related_event_ids", pa.list_(pa.string())),
        ("score_home", pa.int16()),
        ("score_away", pa.int16()),
        ("players_on_pitch_home", pa.int8()),
        ("players_on_pitch_away", pa.int8()),
        ("details", pa.string()),
    ]
)

_TRACKING_SCHEMA = pa.schema(
    [
        ("match_id", pa.string()),
        ("period", pa.int8()),
        ("clock_ms", pa.int32()),
        ("player_id", pa.string()),
        ("x", pa.float64()),
        ("y", pa.float64()),
        ("speed_kmh", pa.float64()),
    ]
)


@dataclass(frozen=True)
class MatchFiles:
    """Reference rows of a match directory, plus raw event and tracking rows."""

    match: Match
    teams: list[Team]
    players: list[Player]
    events: list[dict[str, Any]]
    tracking: list[dict[str, Any]]


def write_match(
    out_dir: Path,
    match: Match,
    teams: Sequence[Team],
    players: Sequence[Player],
    events: Sequence[Event],
    tracking: Sequence[TrackingSample],
) -> list[Path]:
    """Write every output file for one match; returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    reference = {
        "match": match.model_dump(mode="json"),
        "teams": [t.model_dump(mode="json") for t in teams],
        "players": [p.model_dump(mode="json") for p in players],
    }
    paths = [out_dir / name for name in (MATCH_FILE, EVENTS_JSONL, EVENTS_PARQUET,
                                         TRACKING_JSONL, TRACKING_PARQUET)]  # fmt: skip
    paths[0].write_text(json.dumps(reference, indent=2, ensure_ascii=False) + "\n", "utf-8")
    _write_jsonl(paths[1], (e.model_dump_json() for e in events))
    _write_parquet(paths[2], [_event_row(e) for e in events], _EVENT_SCHEMA)
    _write_jsonl(paths[3], (s.model_dump_json() for s in tracking))
    _write_parquet(paths[4], [s.model_dump() for s in tracking], _TRACKING_SCHEMA)
    return paths


def read_match(match_dir: Path, with_tracking: bool = True) -> MatchFiles:
    """Read a match directory. Event and tracking rows stay raw so validation sees them
    exactly as the feed delivered them."""
    reference = json.loads((match_dir / MATCH_FILE).read_text("utf-8"))
    tracking_path = match_dir / TRACKING_JSONL
    return MatchFiles(
        match=Match.model_validate(reference["match"]),
        teams=[Team.model_validate(t) for t in reference["teams"]],
        players=[Player.model_validate(p) for p in reference["players"]],
        events=list(_read_jsonl(match_dir / EVENTS_JSONL)),
        tracking=list(_read_jsonl(tracking_path)) if with_tracking else [],
    )


def write_triggers(match_dir: Path, triggers: Sequence[Trigger]) -> Path:
    """Write detector triggers next to the match files."""
    path = match_dir / TRIGGERS_JSONL
    _write_jsonl(path, (t.model_dump_json() for t in triggers))
    return path


def read_triggers(match_dir: Path) -> list[Trigger]:
    return [Trigger.model_validate(row) for row in _read_jsonl(match_dir / TRIGGERS_JSONL)]


def _event_row(event: Event) -> dict[str, Any]:
    row = event.model_dump(mode="python", exclude={"details"})
    row["details"] = event.details.model_dump_json()
    return row


def _write_jsonl(path: Path, lines: Iterable[str]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(line)
            fh.write("\n")


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _write_parquet(path: Path, rows: list[dict[str, Any]], schema: pa.Schema) -> None:
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, path, compression="zstd")
