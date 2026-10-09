"""Team style parameters and the scenario modifier hook.

Styles and ratings change *choices* only (how often a team presses, how direct it is,
how often it shoots). Pass and shot outcomes always come from the pass-difficulty and
xG models, so difficulty and outcomes agree unless a planted scenario deliberately
shifts them through ``Modifiers``.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pitch_ledger.schema.enums import StyleProfile


@dataclass(frozen=True)
class StyleParams:
    hold_s: float  # mean time on the ball before acting
    forward_bias: float  # preference for forward receivers
    long_rate: float  # chance an open-play pass is a long ball
    press: float  # chance an opposing carrier is pressed (scaled by zone)
    shot_appetite: float  # multiplier on shot decisions
    dribble_rate: float
    carry_rate: float
    counter_rate: float  # chance a regain in the own half starts a counter
    short_goal_kick: float  # chance a goal kick is played short


STYLES: dict[StyleProfile, StyleParams] = {
    StyleProfile.POSSESSION: StyleParams(2.13, 0.25, 0.04, 0.27, 0.9, 0.013, 0.2, 0.1, 0.85),
    StyleProfile.HIGH_PRESS: StyleParams(1.86, 0.4, 0.08, 0.38, 1.0, 0.015, 0.2, 0.25, 0.6),
    StyleProfile.COUNTER: StyleParams(1.95, 0.55, 0.12, 0.24, 0.85, 0.018, 0.24, 0.55, 0.4),
    StyleProfile.LOW_BLOCK: StyleParams(2.29, 0.45, 0.14, 0.19, 1.05, 0.013, 0.2, 0.4, 0.35),
    StyleProfile.DIRECT: StyleParams(1.86, 0.55, 0.2, 0.26, 0.9, 0.013, 0.18, 0.3, 0.2),
}


@dataclass(frozen=True)
class Modifiers:
    """Scenario effects for one team at one moment. Neutral values change nothing.

    ``pass_success_add`` shifts the completion probability away from the difficulty
    model on purpose: that gap is the planted signal the engine must detect. The
    ``focus_*`` fields apply to one player only (``focus_player``).
    """

    pass_success_add: float = 0.0
    turnover_mult: float = 1.0
    press_mult: float = 1.0
    press_win_mult: float = 1.0
    block_shift_m: float = 0.0
    shot_rate_mult: float = 1.0
    top_speed_mult: float = 1.0
    focus_player: str | None = None
    focus_shot_mult: float = 1.0
    focus_quality_mult: float = 1.0
    focus_involvement_mult: float = 1.0


NEUTRAL = Modifiers()

ModifierProvider = Callable[[str, int, int], Modifiers]
"""(team_id, period, clock_ms) -> Modifiers. Built from a scenario by ``matchlab.scenarios``."""


def no_modifiers(team_id: str, period: int, clock_ms: int) -> Modifiers:
    """Baseline provider: no planted change."""
    return NEUTRAL


@dataclass(frozen=True)
class SendOff:
    """A planted straight red card: ``team_id`` loses a player at the first challenge
    after ``period``/``clock_ms``."""

    team_id: str
    period: int
    clock_ms: int
