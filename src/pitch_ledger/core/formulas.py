"""Deterministic metric formulas from docs/event-schema.md ("Derived metrics").

The Match Lab and the engine share these functions so generated outcomes and the
numbers shown on screen agree.
"""

import math

from pitch_ledger.core.pitch import GOAL_CENTRE_Y, GOAL_X, POST_HIGH_Y, POST_LOW_Y

PENALTY_XG = 0.78


def logistic(z: float) -> float:
    """Standard logistic function 1 / (1 + e^-z)."""
    return 1.0 / (1.0 + math.exp(-z))


def distance(x0: float, y0: float, x1: float, y1: float) -> float:
    """Euclidean distance in metres."""
    return math.hypot(x1 - x0, y1 - y0)


def forward_progress(start_x: float, end_x: float) -> float:
    """Forward progress (m) = max(0, end_x - start_x)."""
    return max(0.0, end_x - start_x)


def ball_speed_kmh(distance_m: float, duration_ms: int) -> float:
    """Ball speed (km/h) = distance / (duration_ms / 1000) * 3.6."""
    if duration_ms <= 0:
        raise ValueError("duration_ms must be positive")
    return distance_m / (duration_ms / 1000.0) * 3.6


def pass_completion_probability(
    length_m: float,
    under_pressure: bool,
    forward_m: float,
    high_ball: bool,
    through_or_cross: bool,
) -> float:
    """p_complete from the pass-difficulty model."""
    z = (
        3.2
        - 0.045 * length_m
        - 0.9 * int(under_pressure)
        - 0.02 * forward_m
        - 0.6 * int(high_ball)
        - 0.5 * int(through_or_cross)
    )
    return logistic(z)


def pass_difficulty(
    length_m: float,
    under_pressure: bool,
    forward_m: float,
    high_ball: bool,
    through_or_cross: bool,
) -> float:
    """Pass difficulty (0-100) = 100 * (1 - p_complete)."""
    p = pass_completion_probability(
        length_m, under_pressure, forward_m, high_ball, through_or_cross
    )
    return 100.0 * (1.0 - p)


def goal_angle(x: float, y: float) -> float:
    """Angle in radians between the lines from (x, y) to each post of the attacked goal."""
    a_low = math.atan2(POST_LOW_Y - y, GOAL_X - x)
    a_high = math.atan2(POST_HIGH_Y - y, GOAL_X - x)
    return abs(a_high - a_low)


def expected_goals(
    x: float,
    y: float,
    header: bool = False,
    through_ball_assist: bool = False,
    penalty: bool = False,
) -> float:
    """xG from shot location and context; penalties are fixed at 0.78."""
    if penalty:
        return PENALTY_XG
    d = distance(x, y, GOAL_X, GOAL_CENTRE_Y)
    z = (
        -1.1
        - 0.11 * d
        + 1.6 * goal_angle(x, y)
        - 0.6 * int(header)
        + 0.4 * int(through_ball_assist)
    )
    return logistic(z)
