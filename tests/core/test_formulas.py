"""Formula checks quoted in docs/event-schema.md."""

import math

import pytest

from pitch_ledger.core import formulas
from pitch_ledger.core.pitch import flip


def test_short_unpressured_pass_difficulty_about_8() -> None:
    assert formulas.pass_difficulty(15, False, 0, False, False) == pytest.approx(8, abs=1.5)


def test_long_high_pressured_forward_pass_difficulty_about_65() -> None:
    assert formulas.pass_difficulty(40, True, 25, True, False) == pytest.approx(65, abs=1.5)


def test_worked_example_through_ball() -> None:
    length = formulas.distance(78.0, 36.0, 93.0, 33.0)
    assert length == pytest.approx(15.3, abs=0.05)
    assert formulas.pass_difficulty(length, True, 15.0, False, True) == pytest.approx(31, abs=1)


@pytest.mark.parametrize(("dist", "expected"), [(6, 0.50), (11, 0.22), (25, 0.03)])
def test_central_shot_xg(dist: float, expected: float) -> None:
    assert formulas.expected_goals(105 - dist, 34) == pytest.approx(expected, abs=0.01)


def test_worked_example_shot_xg() -> None:
    assert formulas.expected_goals(94.0, 33.0, through_ball_assist=True) == pytest.approx(
        0.29, abs=0.01
    )


def test_penalty_xg_fixed() -> None:
    assert formulas.expected_goals(94.0, 34.0, penalty=True) == 0.78


def test_ball_speed_and_progress() -> None:
    assert formulas.ball_speed_kmh(15.0, 900) == pytest.approx(60.0)
    assert formulas.forward_progress(50.0, 40.0) == 0.0
    with pytest.raises(ValueError):
        formulas.ball_speed_kmh(1.0, 0)


def test_flip_round_trip() -> None:
    assert flip(45.0, 28.0) == (60.0, 40.0)
    x, y = flip(*flip(12.3, 45.6))
    assert math.isclose(x, 12.3) and math.isclose(y, 45.6)
