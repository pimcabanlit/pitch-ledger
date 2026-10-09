"""Pitch geometry and the coordinate convention from docs/event-schema.md.

Coordinates are metres with the origin bottom-left. Events are stored so the acting
team attacks toward x = 105; tracking is stored so each player's own team does.
"""

PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0
GOAL_X = PITCH_LENGTH_M
GOAL_CENTRE_Y = PITCH_WIDTH_M / 2
POST_LOW_Y = 30.34
POST_HIGH_Y = 37.66
PENALTY_SPOT_X = PITCH_LENGTH_M - 11.0
CENTRE_SPOT = (PITCH_LENGTH_M / 2, PITCH_WIDTH_M / 2)
BOX_DEPTH_M = 16.5
BOX_LOW_Y = 13.84
BOX_HIGH_Y = 54.16


def flip(x: float, y: float) -> tuple[float, float]:
    """Return the same spot seen from the other team: x' = 105 - x, y' = 68 - y."""
    return PITCH_LENGTH_M - x, PITCH_WIDTH_M - y


def in_bounds(x: float, y: float) -> bool:
    """True when a point lies on the pitch (lines included)."""
    return 0.0 <= x <= PITCH_LENGTH_M and 0.0 <= y <= PITCH_WIDTH_M


def in_attacking_box(x: float, y: float) -> bool:
    """True when a point is inside the penalty area the acting team attacks."""
    return x >= PITCH_LENGTH_M - BOX_DEPTH_M and BOX_LOW_Y <= y <= BOX_HIGH_Y


def in_own_box(x: float, y: float) -> bool:
    """True when a point is inside the acting team's own penalty area."""
    return x <= BOX_DEPTH_M and BOX_LOW_Y <= y <= BOX_HIGH_Y
