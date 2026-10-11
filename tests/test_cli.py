"""CLI commands and the match directory format."""

import json
import shutil
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from pitch_ledger.cli import main
from pitch_ledger.matchlab.io import read_match
from pitch_ledger.schema import Event, TrackingSample


@pytest.fixture(scope="module")
def match_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("data") / "M0007"
    assert main(["generate", "--seed", "77", "--home", "T03", "--away", "T05",
                 "--out", str(out)]) == 0  # fmt: skip
    return out


def test_generate_writes_every_file(match_dir: Path) -> None:
    names = {p.name for p in match_dir.iterdir()}
    assert names == {"match.json", "events.jsonl", "events.parquet", "tracking.jsonl",
                     "tracking.parquet"}  # fmt: skip


def test_match_id_taken_from_folder_and_rows_round_trip(match_dir: Path) -> None:
    files = read_match(match_dir)
    assert files.match.match_id == "M0007"
    assert (files.match.home_team_id, files.match.away_team_id) == ("T03", "T05")
    assert len(files.players) == 40 and len(files.teams) == 2
    events = [Event.model_validate(e) for e in files.events]
    assert all(e.match_id == "M0007" for e in events)
    assert json.loads(events[5].model_dump_json()) == files.events[5]
    TrackingSample.model_validate(files.tracking[0])


def test_parquet_matches_jsonl(match_dir: Path) -> None:
    files = read_match(match_dir)
    events = pq.read_table(match_dir / "events.parquet").to_pylist()
    assert len(events) == len(files.events)
    assert [e["event_id"] for e in events] == [e["event_id"] for e in files.events]
    assert json.loads(events[1]["details"]) == files.events[1]["details"]
    assert events[1]["feed_ts_utc"].isoformat().startswith(files.events[1]["feed_ts_utc"][:19])
    tracking = pq.read_table(match_dir / "tracking.parquet")
    assert tracking.num_rows == len(files.tracking)
    assert tracking.column_names == ["match_id", "period", "clock_ms", "player_id", "x", "y",
                                     "speed_kmh"]  # fmt: skip


def test_validate_passes_on_generated_match(
    match_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["validate", str(match_dir), "--no-realism"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_validate_fails_on_corrupted_rows(
    match_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "M0007"
    broken.mkdir()
    for name in ("match.json", "tracking.jsonl"):
        (broken / name).write_bytes((match_dir / name).read_bytes())
    lines = (match_dir / "events.jsonl").read_text("utf-8").splitlines()
    row = json.loads(lines[10])
    row["start_x"] = 120.0
    lines[10] = json.dumps(row)
    del lines[20]
    (broken / "events.jsonl").write_text("\n".join(lines) + "\n", "utf-8")
    assert main(["validate", str(broken), "--no-realism", "--no-tracking"]) == 1
    out = capsys.readouterr().out
    assert "ROW-02" in out and "SEQ-01" in out and "FAIL" in out


def test_realism_prints_every_band(match_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["realism", str(match_dir)])
    out = capsys.readouterr().out
    for metric in ("passes_both_teams", "pass_accuracy_pct", "shots_both_teams", "goals",
                   "tackles_both_teams", "distance_outfield_km", "top_speed_kmh",
                   "shot_speed_kmh"):  # fmt: skip
        assert metric in out


def test_unknown_or_mismatched_scenarios_rejected(tmp_path: Path) -> None:
    assert main(["generate", "--scenario", "bogus", "--out", str(tmp_path)]) == 2
    # red_card targets T04, which is not playing in the default T01 v T02 pairing
    assert main(["generate", "--scenario", "red_card", "--out", str(tmp_path)]) == 2
    assert not any(tmp_path.iterdir())


def test_replay_writes_the_ledger(
    match_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copy = tmp_path / "M0007"
    shutil.copytree(match_dir, copy)
    assert main(["replay", str(copy), "--approve", "--local-stats"]) == 0
    out = capsys.readouterr().out
    assert "M0007 replay:" in out and "pending 0" in out and "ledger ->" in out
    run = json.loads((copy / "ledger" / "run.json").read_text("utf-8"))
    assert run["provider"] == "offline" and run["producer"] == "producer_demo"
    assert run["overlays"] == 15 * run["counts"]["status"]["aired"] > 0
    assert (copy / "triggers.jsonl").exists()


def test_history_build_show_and_a_replay_with_the_season(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    assert main(["generate", "--seed", "4211", "--out", str(data / "M0001"),
                 "--kickoff", "2026-08-22T14:00:00+00:00"]) == 0  # fmt: skip
    assert main(["generate", "--scenario", "late_collapse", "--seed", "4212",
                 "--out", str(data / "M0002")]) == 0  # fmt: skip
    capsys.readouterr()
    assert main(["history", "build", "--data", str(data), "--history", "file"]) == 0
    assert (data / "history" / "records.jsonl").exists()
    assert main(["history", "show", "--data", str(data), "--history", "file"]) == 0
    out = capsys.readouterr().out
    assert "Harbour Coast League 2026-27: 2 matches" in out and "T01  2  1  1  0" in out
    assert main(["replay", str(data / "M0002"), "--approve", "--local-stats",
                 "--history", "file"]) == 0  # fmt: skip
    assert "season: earlier matches M0001" in capsys.readouterr().out
    run = json.loads((data / "M0002" / "ledger" / "run.json").read_text("utf-8"))
    assert run["season_matches"] == ["M0001"]
    assert run["counts"]["status"].get("blocked", 0) == 0
