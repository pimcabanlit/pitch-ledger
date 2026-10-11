"""Evaluation scorecard: sections on M0002, the checker itself, gates and the CLI."""

import asyncio
import json
from pathlib import Path

import pytest

from pitch_ledger.cli import main
from pitch_ledger.history import InMemoryHistory, build_record
from pitch_ledger.matchlab.build import build_match
from pitch_ledger.matchlab.scenarios import load_manifest
from pitch_ledger.scorecard import (
    CHECKS,
    _Match,
    _run_match,
    _spec,
    accuracy_section,
    coverage_section,
    detection_section,
    evaluation_rows,
    planted_section,
)


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> list[_Match]:
    """M0002 against its season so far (M0001's record), so season facts are checked too."""
    first, entry = (next(e for e in load_manifest() if e.match_id == m) for m in ("M0001", "M0002"))
    built = build_match(_spec(entry))
    earlier, earlier_tracking = build_match(_spec(first))
    history = InMemoryHistory([build_record(earlier.match, earlier.events, earlier_tracking)])
    run = _run_match(entry, built, tmp_path_factory.mktemp("scorecard"), None, history)
    return [asyncio.run(run)]


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
    trick = next(t for t in runs[0].clean.triggers if t.metrics.get("player_goals") == 3)
    assert trick.metrics["season_hat_tricks_before"] == 0  # checked by the accuracy section
    assert trick.context["season"]["matches"] == ["M0001"]


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


def test_evaluation_rows_carry_the_moment_the_context_and_each_check(runs: list[_Match]) -> None:
    m = runs[0]
    rows = evaluation_rows(m.clean.ledger, m.sim, m.tracking, m.season)
    assert len(rows) == accuracy_section(runs)["aired_insights"]
    assert all(r[name] == "pass" for r in rows for name in CHECKS)
    momentum = next(r for r in rows if r["insight_type"] == "momentum_shift")
    assert "(Corrowmere Town)" in momentum["query"]  # a team moment names the team
    assert momentum["context"].startswith("Metrics computed in code (exact): ")
    assert "control_before=" in momentum["context"] and "Evidence events: M0002-E" in momentum[
        "context"]  # fmt: skip
    insight = m.clean.ledger.get_insight(momentum["insight_id"])
    assert insight is not None and momentum["response"].startswith(insight.headline)


def test_evaluation_rows_fail_a_tampered_insight(runs: list[_Match]) -> None:
    m = runs[0]
    ledger = m.clean.ledger
    insight = next(i for i in ledger.insights() if i.insight_type.value == "momentum_shift")
    tampered = insight.model_copy(update={"metrics": {**insight.metrics, "turnovers_after": 99}})
    ledger.put_insight(tampered)
    try:
        row = next(r for r in evaluation_rows(ledger, m.sim, m.tracking, m.season)
                   if r["insight_id"] == insight.insight_id)  # fmt: skip
    finally:
        ledger.put_insight(insight)
    assert row["recompute"] == "fail" and "turnovers_after=99" in row["problems"]
