"""Editorial quality: every template passes the form rules, modes say it differently, and
the copy degrades gracefully when context metrics are missing (older triggers)."""

import re

import pytest

from pitch_ledger.agents import copy
from pitch_ledger.agents.editorial import (
    draft_issues,
    editorial_issues,
    figures,
    mode_issues,
    sentences,
)
from pitch_ledger.engine.season import has_season_facts
from pitch_ledger.validation.insight_rules import unsupported_numbers
from tests.agents.conftest import Case

CONTEXT_METRICS = {
    "player_goals", "team_shots", "team_xg", "opponent_xg", "shot_speed_kmh",
    "seconds_from_regain", "opponent_shots_after", "opponent_xg_after", "score_home",
    "score_away", "minutes_left", "shots_previous", "team_shot_share", "on_target_home",
    "on_target_away", "xg_second_half_home", "xg_second_half_away", "xg",
}  # fmt: skip
"""Metrics added for context; a trigger recorded before them has none of them (except the
ones a rule always had, which are restored below)."""

ORIGINAL = {
    "goal": {"xg", "minute", "score_home", "score_away"},
    "shot_speed": {"shot_speed_kmh"},
    "half_time_recap": {"score_home", "score_away"},
    "full_time_recap": {"score_home", "score_away"},
}


def _figures(text: str) -> int:
    """Numbers on screen, a score or a before → after pair counting as one figure."""
    pair = r"\d+(?:[.,]\d+)?\s*%?\s*(?:-|→)\s*\d+(?:[.,]\d+)?"
    text = re.sub(pair, "#", text)
    return len(re.findall(r"#|\d+(?:[.,]\d+)?", text))


@pytest.mark.parametrize(
    ("text", "kind", "problem"),
    [
        ("One two three four five six seven eight nine ten eleven", "headline", "words"),
        ("A. B. C. D. One. Two. Three.", "explanation", "sentences"),
        ("x" * 141, "overlay", "characters"),
        ("What a strike from P!", "overlay", "cliché"),
        ("His best season yet.", "overlay", "unsupported"),
        ("They lost it due to the press.", "overlay", "unsupported"),
        ("Así cae por una lesión.", "overlay", "unsupported"),
        ("Same line. Same line.", "overlay", "repeated"),
    ],
)
def test_editorial_check_catches_each_problem(text: str, kind: str, problem: str) -> None:
    language = "es" if "lesión" in text else "en"
    issues = editorial_issues(text, kind, language)  # type: ignore[arg-type]
    assert any(problem in i for i in issues), issues


@pytest.mark.parametrize(
    ("text", "mode", "problem"),
    [  # real model output from a Foundry replay of M0002, before these rules existed
        ("L. Haugland opens scoring for Northbridge Athletic at 4.7 minutes", None, "minutes"),
        ("H. Amaral levels score for Corrowmere Town at 9th minute", None, "ordinals"),
        ("Corrowmere control drops 49%→6%, possession 63.6%→26.5%", None, "index"),
        ("Haugland's hat-trick puts Northbridge 4-1 up, reflecting our dominance.", None, "first"),
        (
            "Haugland's hat-trick puts Northbridge 4-1 up, reflecting their dominance.",
            None,
            "dominan",
        ),
        ("Shots: 5 → 5 in 15 minutes (83% of team shots)", "single_metric", "figures"),
    ],
)
def test_house_style_catches_real_model_slips(text: str, mode: str | None, problem: str) -> None:
    issues = editorial_issues(text, "overlay") + (mode_issues(text, mode) if mode else [])
    assert any(problem in i for i in issues), issues


def test_sentence_split_ignores_initials_and_decimals() -> None:
    assert (
        len(sentences("L. Haugland scores from a 0.23 xG chance. It completes a hat-trick.")) == 2
    )
    assert draft_issues("Haugland scores", "B. Engebret assists. It is a 0.23 xG chance.") == []
    assert any("repeats the headline" in i for i in draft_issues("A scores", "A scores. Then."))


def test_every_template_passes_the_editorial_check(cases: list[Case]) -> None:
    for case in cases:
        key = copy.copy_key(case.trigger)
        values = {**case.trigger.metrics, **case.facts.names()}
        season = has_season_facts(case.trigger.metrics)  # the season may then be named
        headline, explanation = copy.headline(key, values), copy.explanation(key, values)
        assert draft_issues(headline, explanation, season=season) == [], key
        for mode in copy.MODES:
            for language in copy.LANGUAGES:
                text = copy.mode_text(key, mode, language, values)
                issues = editorial_issues(text, "overlay", language, season=season)
                assert issues == [], (key, mode, text)


def test_modes_differ_and_casual_stays_light(cases: list[Case]) -> None:
    for case in cases:
        key = copy.copy_key(case.trigger)
        values = {**case.trigger.metrics, **case.facts.names()}
        for language in copy.LANGUAGES:
            texts = [copy.mode_text(key, m, language, values) for m in copy.MODES]
            assert len(set(texts)) == len(texts), (key, language, texts)
        casual = copy.mode_text(key, "casual", "en", values)
        assert _figures(casual) <= 1, (key, casual)
        assert _figures(copy.mode_text(key, "single_metric", "en", values)) == 1, key


def test_goal_copy_says_what_the_goal_does(cases: list[Case]) -> None:
    goals = [c for c in cases if c.trigger.rule_id == "goal" and c.trigger.match_id == "M0002"]
    heads = [copy.headline("goal", {**c.trigger.metrics, **c.facts.names()}) for c in goals]
    assert heads[0].endswith("opens the scoring at 1-0")
    assert heads[1].endswith("levels the score at 1-1")
    assert "hat-trick" in heads[-1]  # the third goal of one player leads the headline
    last = goals[-1]
    expl = copy.explanation("goal", {**last.trigger.metrics, **last.facts.names()})
    assert "seconds after" in expl  # scored soon after winning the ball


def test_older_triggers_without_context_metrics_still_render(cases: list[Case]) -> None:
    """Backward compatibility: strip every context metric (as a trigger recorded before them
    would be) and the copy still renders, with numbers only from what remains."""
    for case in cases:
        keep = ORIGINAL.get(case.trigger.rule_id, set())
        metrics = {
            k: v for k, v in case.trigger.metrics.items() if k not in CONTEXT_METRICS or k in keep
        }
        values = {**metrics, **case.facts.names()}
        key = copy.copy_key(case.trigger)
        text = f"{copy.headline(key, values)} {copy.explanation(key, values)}"
        used = copy.used_metrics(key, metrics, values)
        assert unsupported_numbers(text, [metrics[k] for k in used]) == [], (key, text)
        for mode in copy.MODES:
            for language in copy.LANGUAGES:
                rendered = copy.mode_text(key, mode, language, values)
                assert "{" not in rendered
                assert unsupported_numbers(rendered, metrics.values(), language) == []


def test_team_moment_without_a_player_uses_the_team_line(cases: list[Case]) -> None:
    case = next(c for c in cases if c.trigger.rule_id == "momentum_shift")
    values = {**case.trigger.metrics, **case.facts.names()}
    values.pop("player")
    text = copy.mode_text(copy.copy_key(case.trigger), "player_focus", "en", values)
    assert "players" in text and "{" not in text


def test_recap_with_no_shots_reads_as_even() -> None:
    values = {"home": "A", "away": "B", "score_home": 0, "score_away": 0, "shots_home": 0,
              "shots_away": 0, "xg_home": 0, "xg_away": 0, "possession_home": 50.0,
              "possession_away": 50.0, "passes_home": 0, "passes_away": 0,
              "pass_accuracy_home": 0.0, "pass_accuracy_away": 0.0}  # fmt: skip
    text = copy.explanation("half_time_recap", values)
    assert text.startswith("There has been little between them")
    assert draft_issues(copy.headline("half_time_recap", values), text) == []


def test_signals_give_models_the_context_in_words(cases: list[Case]) -> None:
    case = next(c for c in cases if c.trigger.rule_id == "penalty_awarded")
    assert any(s.startswith("pen_stakes: Scoring it would") for s in case.facts.signals)
    goals = [c for c in cases if c.trigger.rule_id == "goal" and c.trigger.match_id == "M0002"]
    before = [next(s for s in c.facts.signals if s.startswith("goal_before:")) for c in goals]
    assert before[0] == "goal_before: the match was goalless before it"
    assert before[2] == "goal_before: the score was level before it"  # the 2-1 at 30.5'
    assert before[3].endswith("were already ahead before it")
    for c in cases:
        for s in c.facts.signals:
            assert unsupported_numbers(s, c.trigger.metrics.values()) == [], s


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("Corrowmere control 49 → 6", 1),
        ("Control de Corrowmere: de 49 a 6", 1),  # Spanish writes the arrow as a word
        ("Posesión del 64% al 27%", 1),
        ("Bumaba ang control mula 49 hanggang 6", 1),  # Filipino
        ("Kontrol 49 pababa sa 6", 1),
        ("Haugland marca en el 5' y pone el 1-0", 2),  # a minute and a score: two figures
        ("Haugland marca y pone el 3 a 1", 1),
    ],
)
def test_a_before_after_pair_counts_once_in_every_language(text: str, count: int) -> None:
    assert figures(text) == count


def test_dominating_and_gendered_pronouns_are_caught() -> None:
    text = "After already dominating chances, L. Haugland scores his second."
    issues = editorial_issues(text, "overlay")
    assert any("dominat" in i for i in issues)
    assert any("gendered pronouns" in i for i in issues)
    assert editorial_issues("L. Haugland scores again; the scorer's second.", "overlay") == []
