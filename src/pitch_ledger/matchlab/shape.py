"""Formation slots and team shape, shared by the event generator and tracking.

Positions are in the team's own frame (it attacks toward x = 105). Slot order matches
``Club.lineup``: goalkeeper, back line right to left, midfield, attack. Facing +x, a
team's right touchline is y = 0.
"""

from pitch_ledger.core.pitch import PITCH_LENGTH_M, PITCH_WIDTH_M
from pitch_ledger.schema.enums import StyleProfile

Point = tuple[float, float]

FORMATION_SLOTS: dict[str, list[Point]] = {
    "4-3-3": [(5, 34), (32, 8), (26, 24), (26, 44), (32, 60), (42, 34), (54, 22), (54, 46),
              (74, 9), (80, 34), (74, 59)],
    "4-2-3-1": [(5, 34), (32, 8), (26, 24), (26, 44), (32, 60), (42, 26), (42, 42), (70, 10),
                (64, 34), (70, 58), (80, 34)],
    "4-4-2": [(5, 34), (32, 8), (26, 24), (26, 44), (32, 60), (56, 9), (48, 26), (48, 42),
              (56, 59), (78, 28), (78, 40)],
    "5-4-1": [(5, 34), (36, 6), (25, 20), (23, 34), (25, 48), (36, 62), (54, 10), (47, 26),
              (47, 42), (54, 58), (76, 34)],
}  # fmt: skip

BLOCK_DEPTH_M: dict[StyleProfile, float] = {
    StyleProfile.HIGH_PRESS: 8.0,
    StyleProfile.POSSESSION: 4.0,
    StyleProfile.COUNTER: -4.0,
    StyleProfile.DIRECT: 0.0,
    StyleProfile.LOW_BLOCK: -9.0,
}
"""How far up the pitch a team holds its shape when defending (metres)."""


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def slot_position(
    formation: str,
    slot: int,
    ball_x: float,
    ball_y: float,
    in_possession: bool,
    style: StyleProfile,
    depth_shift: float = 0.0,
) -> Point:
    """Where a slot stands given the ball, in the team's own frame.

    ``ball_x``/``ball_y`` are in the same (own) frame. In possession the shape pushes up and
    spreads; out of possession it drops, narrows toward the ball and holds a style-based
    block depth, moved by ``depth_shift`` metres (a scenario pushing the press higher).
    """
    base_x, base_y = FORMATION_SLOTS[formation][slot]
    if slot == 0:  # goalkeeper
        x = 5.0 + max(0.0, ball_x - 52.5) * 0.25
        return round(_clamp(x, 1.0, 20.0), 1), round(_clamp(34 + (ball_y - 34) * 0.15, 28, 40), 1)
    if in_possession:
        x = base_x * 0.7 + ball_x * 0.38
        y = base_y + (ball_y - 34.0) * 0.25
    else:
        x = base_x * 0.6 + ball_x * 0.32 + BLOCK_DEPTH_M[style] - 6.0 + depth_shift
        y = 34.0 + (base_y - 34.0) * 0.8 + (ball_y - 34.0) * 0.35
    return (
        round(_clamp(x, 2.0, PITCH_LENGTH_M - 2.0), 1),
        round(_clamp(y, 1.0, PITCH_WIDTH_M - 1.0), 1),
    )
