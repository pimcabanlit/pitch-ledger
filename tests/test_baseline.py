"""Phase 1 acceptance: the baseline match (scenario none, seed 4211) is valid, realistic
and reproducible byte for byte."""

from pathlib import Path

import pytest

from pitch_ledger.cli import main
from pitch_ledger.matchlab.generator import MatchSpec, simulate_match
from pitch_ledger.matchlab.io import read_match
from pitch_ledger.schema import Event, TrackingSample
from pitch_ledger.validation import measure, validate_match

FILES = ("match.json", "events.jsonl", "events.parquet", "tracking.jsonl", "tracking.parquet")
EVENT_METRICS = {
    "passes_both_teams", "passes_home", "passes_away", "pass_accuracy_pct",
    "shots_both_teams", "goals", "tackles_both_teams",
}  # fmt: skip


def _generate(root: Path) -> Path:
    out = root / "M0001"
    assert main(["generate", "--scenario", "none", "--seed", "4211", "--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return _generate(tmp_path_factory.mktemp("a")), _generate(tmp_path_factory.mktemp("b"))


def test_byte_identical_across_runs(runs: tuple[Path, Path]) -> None:
    first, second = runs
    for name in FILES:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_passes_every_validation_rule(runs: tuple[Path, Path]) -> None:
    files = read_match(runs[0])
    report = validate_match(files.match, files.players, files.events, files.tracking)
    assert report.results == [], report.counts()
    assert report.events_checked == len(files.events) > 2000
    assert report.tracking_checked == len(files.tracking) > 100_000


def test_inside_every_realism_band(runs: tuple[Path, Path]) -> None:
    files = read_match(runs[0])
    checks = measure(
        files.match,
        files.players,
        [Event.model_validate(e) for e in files.events],
        [TrackingSample.model_validate(s) for s in files.tracking],
    )
    assert len(checks) == 10
    out = {c.band.metric: c.summary() for c in checks if not c.ok}
    assert out == {}


def test_most_seeds_land_in_event_bands() -> None:
    """Guards against tuning drift: the baseline pairing stays realistic across seeds."""
    in_band = 0
    for seed in range(4200, 4220):
        sim = simulate_match(MatchSpec(seed=seed))
        checks = measure(sim.match, sim.players, sim.events, [])
        in_band += all(c.ok for c in checks if c.band.metric in EVENT_METRICS)
    assert in_band >= 15
