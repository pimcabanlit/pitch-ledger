"""Evaluation scorecard: sections on M0002, the checker itself, gates and the CLI."""

import asyncio
import json
from pathlib import Path

import pytest

from pitch_ledger.cli import main
from pitch_ledger.matchlab.scenarios import load_manifest
from pitch_ledger.scorecard import (
    _Match,
    _run_match,
    accuracy_section,
    coverage_section,
    detection_section,
    planted_section,
)


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> list[_Match]:
    entry = next(e for e in load_manifest() if e.match_id == "M0002")
    return [asyncio.run(_run_match(entry, tmp_path_factory.mktemp("scorecard"), None))]


def test_sections_on_m0002(runs: list[_Match]) -> None:
    detection = detection_section(runs, sweep_seeds=1)
    assert detection["detection_rate"] == 1.0 and detection["false_alerts"] == 0
    assert detection["demo"][0]["detected"] is not None
    accuracy = accuracy_section(runs)
    assert accuracy["aired_insights"] > 10 and accuracy["factual_accuracy"] == 1.0
    assert accuracy["overlays_checked"] == 15 * accuracy["aired_insights"]
    planted = planted_section(runs)
    assert planted["planted"] >= 8 and planted["catch_rate"] == 1.0
    assert planted["wrong_numbers_passed"] == 0 and planted["missed"] == []
    assert coverage_section(runs)["coverage"] == 1.0


def test_accuracy_check_flags_a_wrong_number(runs: list[_Match]) -> None:
    ledger = runs[0].clean.ledger
    insight = next(i for i in ledger.insights() if i.insight_type.value == "momentum_shift")
    tampered = insight.model_copy(update={"metrics": {**insight.metrics, "turnovers_after": 99}})
    ledger.put_insight(tampered)
    try:
        accuracy = accuracy_section(runs)
    finally:
        ledger.put_insight(insight)
    assert accuracy["accurate_insights"] == accuracy["aired_insights"] - 1
    assert any("turnovers_after=99" in p for p in accuracy["problems"])


def test_evaluate_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "scorecard.json"
    assert main(["evaluate", "--match", "M0004", "--sweep", "1", "--out", str(out)]) == 0
    card = json.loads(out.read_text("utf-8"))
    assert card["passed"] is True and card["matches"] == ["M0004"]
    assert set(card) >= {"detection", "accuracy", "planted", "coverage", "outcomes", "usage"}
    printed = capsys.readouterr().out
    assert "[PASS] every planted error caught" in printed and "FAIL" not in printed
    assert main(["evaluate", "--match", "M9999", "--out", str(out)]) == 2
