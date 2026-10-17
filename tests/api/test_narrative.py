"""The fan story behind "Tell me more": three beats per audience mode and language."""

import asyncio
import re
from pathlib import Path

import pytest

from pitch_ledger.agents.replay import ReplayOptions, replay_match
from pitch_ledger.api.match_centre import MatchCentre
from pitch_ledger.api.narrative import STORY_MODES, build_narratives
from pitch_ledger.cli import main
from pitch_ledger.ledger import FileLedger
from pitch_ledger.schema import Insight


@pytest.fixture(scope="module")
def red_card_match(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The planted red-card match, every insight approved offline."""
    match_dir = tmp_path_factory.mktemp("story") / "M0004"
    assert main(["generate", "--scenario", "red_card", "--seed", "4213", "--home", "T03",
                 "--away", "T04", "--out", str(match_dir)]) == 0  # fmt: skip
    options = ReplayOptions(approve=True, provider="offline", stats="local")
    asyncio.run(replay_match(match_dir, options))
    return match_dir


def _stories(match_dir: Path) -> dict[str, tuple[Insight, dict[tuple[str, str], object]]]:
    ledger = FileLedger(match_dir / "ledger")
    data = MatchCentre().get(match_dir)
    out = {}
    for insight in ledger.insights("M0004"):
        trigger = ledger.get_trigger(insight.trigger_id or "")
        stories = build_narratives(data, insight, trigger, ledger.overlays(insight.insight_id))
        out[insight.insight_id] = (insight, {(n.mode, n.language): n for n in stories})
    return out


def test_every_aired_insight_gets_a_story_in_every_mode_and_language(
    red_card_match: Path,
) -> None:
    stories = _stories(red_card_match)
    assert len(stories) > 10
    for insight, by_key in stories.values():
        assert set(by_key) == {(m, lang) for m in STORY_MODES for lang in ("en", "es", "fil")}
        for (mode, language), story in by_key.items():
            assert story.buildup and story.moment and story.outlook, (insight.insight_id, mode)
            assert story.title
            if language == "en":
                assert story.title == insight.headline


def test_casual_reads_in_plain_words_and_analyst_carries_the_figures(
    red_card_match: Path,
) -> None:
    def figures(text: str) -> int:
        return len(re.findall(r"\d+(?:[.,]\d+)?", text))

    for _, by_key in _stories(red_card_match).values():
        casual, analyst = by_key["casual", "en"], by_key["analyst", "en"]
        assert figures(casual.buildup) <= figures(analyst.buildup)
        assert "xG" not in casual.buildup + casual.outlook


def test_red_card_story_sets_the_stakes(red_card_match: Path) -> None:
    stories = _stories(red_card_match)
    [(red, by_key)] = [(i, s) for i, s in stories.values() if "sent off" in i.headline]
    outlook = by_key["casual", "en"].outlook
    assert outlook.startswith("Tarnwick United must chase the game with 10 for the last ")
    assert by_key["casual", "es"].outlook.startswith("Tarnwick United tendrá que remontar")
    assert by_key["casual", "fil"].title.startswith("Red card kay ")
    after = next(s for i, s in stories.values() if i.period == 2 and i.clock_ms > red.clock_ms
                 and "with 10" in i.headline)  # fmt: skip
    assert "since H. Barreiro's red card" in after["analyst", "en"].buildup
