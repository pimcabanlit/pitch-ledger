"""Copy templates render for every processed rule, mode and language, using only metric
numbers."""

from pitch_ledger.agents import copy
from pitch_ledger.validation.insight_rules import unsupported_numbers
from tests.agents.conftest import Case


def test_demo_matches_cover_every_processed_rule(cases: list[Case]) -> None:
    rules = {c.trigger.rule_id for c in cases}
    assert {"momentum_shift", "goal", "sending_off", "sending_off_impact", "player_form",
            "half_time_recap", "full_time_recap", "shot_speed", "top_speed"} <= rules  # fmt: skip
    keys = {copy.copy_key(c.trigger) for c in cases}
    assert {"momentum_shift/build_up", "momentum_shift/pressing"} <= keys


def test_catalogue_complete() -> None:
    for key, rule in copy.CATALOGUE.items():
        for mode in copy.MODES:
            assert set(rule.modes[mode]) == set(copy.LANGUAGES), (key, mode)


def test_every_mode_and_language_renders_with_metric_numbers_only(cases: list[Case]) -> None:
    for case in cases:
        key = copy.copy_key(case.trigger)
        values = {**case.trigger.metrics, **case.facts.names()}
        metrics = case.trigger.metrics
        for mode in copy.MODES:
            for language in copy.LANGUAGES:
                text = copy.mode_text(key, mode, language, values)
                assert text and "{" not in text
                assert unsupported_numbers(text, metrics.values(), language) == [], (key, text)


def test_insight_text_uses_only_its_metric_keys(cases: list[Case]) -> None:
    for case in cases:
        key = copy.copy_key(case.trigger)
        values = {**case.trigger.metrics, **case.facts.names()}
        used = copy.used_metrics(key, case.trigger.metrics)
        text = f"{copy.headline(key, values)} {copy.explanation(key, values)}"
        allowed = [case.trigger.metrics[k] for k in used]
        assert unsupported_numbers(text, allowed) == [], (key, text)


def test_spanish_uses_decimal_comma() -> None:
    assert copy.fill("{x} {y:0}", {"x": 6.2, "y": 61.4}, "es") == "6,2 61"
    assert copy.fill("{x} {y:0}", {"x": 6.2, "y": 61.4}, "fil") == "6.2 61"
    assert unsupported_numbers("PPDA 6,2", [6.2], "es") == []
    assert unsupported_numbers("PPDA 6,3", [6.2], "es") == ["6.3"]


def test_pressing_falls_back_when_ppda_missing() -> None:
    values = {"team": "A", "opp": "B", "player": "P", "control_before": 50, "control_after": 20,
              "onset_minute": 61.2, "pass_accuracy_before": 80.0, "pass_accuracy_after": 60.0,
              "turnovers_after": 5, "own_half_turnovers_after": 3,
              "minutes_since_onset": 8.0}  # fmt: skip
    assert "build-up" in copy.headline("momentum_shift/pressing", values)
    assert "PPDA" not in copy.mode_text("momentum_shift/pressing", "analyst", "en", values)
    assert "opponent_ppda_before" not in copy.used_metrics("momentum_shift/pressing", values)  # type: ignore[arg-type]
