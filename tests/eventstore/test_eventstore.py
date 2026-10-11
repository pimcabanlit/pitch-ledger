"""Event store: table definitions, the KQL script, row builders, file and ADX adapters."""

import json
from pathlib import Path

import pytest

from pitch_ledger.cli import main
from pitch_ledger.engine.windows import MatchEngine
from pitch_ledger.eventstore import TABLES, FileEventStore, create_script, project
from pitch_ledger.eventstore.rows import (
    FeedHealthMeter,
    export_match,
    team_state_row,
    team_state_rows,
)
from pitch_ledger.infra_adapters.adx import MAX_REQUEST_BYTES, AdxEventStore, encode
from pitch_ledger.infra_adapters.stores import StoreSettings, open_store
from pitch_ledger.matchlab.io import read_match
from pitch_ledger.schema import Event

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def match_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("data") / "M0007"
    assert main(["generate", "--seed", "77", "--home", "T03", "--away", "T05",
                 "--out", str(out)]) == 0  # fmt: skip
    return out


def test_committed_kql_script_matches_the_table_definitions() -> None:
    assert (REPO / "infra" / "adx" / "tables.kql").read_text() == create_script()


def test_script_creates_every_table_with_a_mapping_and_policies() -> None:
    script = create_script()
    for table, columns in TABLES.items():
        assert f".create-merge table ['{table}']" in script
        assert f"ingestion json mapping '{table}_json'" in script
        assert f".alter table ['{table}'] policy streamingingestion enable" in script
        assert all(f"['{name}']:" in script for name, _ in columns)


def test_events_table_has_every_feed_column(match_dir: Path) -> None:
    row = read_match(match_dir, with_tracking=False).events[0]
    assert set(row) == {name for name, _ in TABLES["Events"]}


def test_project_keeps_only_table_columns() -> None:
    row = project("StoreCheck", {"check_id": "x", "extra": 1})
    assert row == {"check_id": "x", "written_at": None}


def test_export_match_writes_every_table(match_dir: Path, tmp_path: Path) -> None:
    store = FileEventStore(tmp_path)
    counts = export_match(match_dir, store, recorded_at=1.0)
    store.close()
    files = read_match(match_dir)
    assert counts["Events"] == len(files.events) and counts["Tracking"] == len(files.tracking)
    assert counts["Triggers"] > 0 and counts["InsightStatus"] == 0  # no replay yet
    lines = (tmp_path / "TeamState.jsonl").read_text().splitlines()
    assert len(lines) == counts["TeamState"]
    assert set(json.loads(lines[0])) == {name for name, _ in TABLES["TeamState"]}
    # A finished match also writes its season-history rows.
    assert (counts["MatchResults"], counts["TeamMatchStats"]) == (1, 2)
    assert counts["PlayerMatchStats"] >= 22
    result = json.loads((tmp_path / "MatchResults.jsonl").read_text())
    final = files.events[-1]
    assert (result["score_home"], result["score_away"]) == (
        final["score_home"],
        final["score_away"],
    )
    assert (result["match_id"], result["season"]) == ("M0007", "2026-27")


def test_team_state_rows_are_the_engine_windows(match_dir: Path) -> None:
    files = read_match(match_dir, with_tracking=False)
    events = [Event.model_validate(e) for e in files.events]
    rows = team_state_rows(files.match, events)
    engine = MatchEngine(files.match, events)
    home = files.match.home_team_id
    expected = team_state_row("M0007", engine.team_window(home, 2, 10 * 60_000, 15 * 60_000))
    assert expected in rows
    assert expected["minute"] == 60.0


def test_feed_health_meter_closes_finished_minutes_only() -> None:
    meter = FeedHealthMeter()
    for i in range(10):
        meter.add("M1", 120.0 + i, float(100 + i))
    meter.add("M1", 185.0, None)  # next minute, no latency
    finished = meter.take(before=181.0)
    assert len(finished) == 1
    assert finished[0]["messages"] == 10 and finished[0]["p50_ms"] == 104
    assert finished[0]["window_start"].startswith("1970-01-01T00:02:00")
    rest = meter.take()
    assert rest[0]["messages"] == 1 and rest[0]["p95_ms"] is None


def test_file_store_rejects_unknown_tables(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        FileEventStore(tmp_path).append("Nope", [{}])


def test_encode_splits_large_payloads_under_the_streaming_limit() -> None:
    rows = [{"check_id": "x" * 1000, "written_at": None} for _ in range(8000)]
    chunks = encode("StoreCheck", rows)
    assert len(chunks) >= 2 and all(len(c) <= MAX_REQUEST_BYTES for c in chunks)
    assert sum(c.count(b"\n") for c in chunks) == 8000


def test_adx_store_buffers_then_flushes_by_size_age_and_close() -> None:
    sent: list[tuple[str, int]] = []
    now = [0.0]
    closed = []
    store = AdxEventStore(
        "db", lambda table, payload: sent.append((table, payload.count(b"\n"))),
        max_rows=3, max_age_s=5, clock=lambda: now[0], on_close=lambda: closed.append(1),
    )  # fmt: skip
    store.append("Tracking", [{"match_id": "M1"}] * 2)
    assert sent == []  # buffered
    store.append("Tracking", [{"match_id": "M1"}])
    assert sent == [("Tracking", 3)]  # size
    store.append("Events", [{"event_id": "e"}])
    now[0] = 6.0
    store.append("Events", [{"event_id": "f"}])
    assert sent[-1] == ("Events", 2)  # age
    store.append("FeedHealth", [{"match_id": "M1"}])
    store.close()
    assert sent[-1] == ("FeedHealth", 1) and closed == [1]
    assert store.rows_sent == {"Tracking": 3, "Events": 2, "FeedHealth": 1}


def test_adx_store_fails_open_on_live_paths_and_raises_otherwise() -> None:
    def refuse(table: str, payload: bytes) -> None:
        if table == "Tracking":
            raise PermissionError("Principal is not authorized to ingest")

    live = AdxEventStore("db", refuse, fail_open=True)
    live.append("Tracking", [{"match_id": "M1"}] * 2)
    live.append("Events", [{"event_id": "e"}])
    live.flush()  # carries on
    assert live.rows_failed == {"Tracking": 2} and live.rows_sent == {"Events": 1}
    strict = AdxEventStore("db", refuse)
    strict.append("Tracking", [{"match_id": "M1"}])
    with pytest.raises(PermissionError):
        strict.flush()


def test_store_settings_default_to_files_and_adx_needs_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("PITCH_LEDGER_STORE", "ADX_CLUSTER_URI", "ADX_DATABASE",
                 "PITCH_LEDGER_ADX_CLUSTER_URI", "PITCH_LEDGER_ADX_DATABASE"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    settings = StoreSettings(_env_file=None, store_dir=tmp_path)  # type: ignore[call-arg]
    assert isinstance(open_store(settings), FileEventStore)
    with pytest.raises(ValueError, match="ADX_CLUSTER_URI"):
        open_store(StoreSettings(_env_file=None, store="adx"))  # type: ignore[call-arg]


def test_cli_store_and_listen_write_the_file_store(
    match_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PITCH_LEDGER_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("PITCH_LEDGER_FEED_FILE", str(tmp_path / "feed.jsonl"))
    assert main(["store", str(match_dir), "--store", "file", "--no-tracking"]) == 0
    assert (tmp_path / "store" / "Triggers.jsonl").exists()
    assert main(["stream", str(match_dir), "--feed", "file", "--speed", "1000",
                 "--minutes", "1"]) == 0  # fmt: skip
    assert main(["listen", "--feed", "file", "--from-start", "--idle", "0.3",
                 "--out", str(tmp_path / "live"), "--store", "file"]) == 0  # fmt: skip
    health = [
        json.loads(x) for x in (tmp_path / "store" / "FeedHealth.jsonl").read_text().splitlines()
    ]
    assert sum(h["messages"] for h in health) > 1000
