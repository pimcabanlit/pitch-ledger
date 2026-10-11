"""Enumerations from docs/event-schema.md. All values are lowercase snake_case."""

from enum import StrEnum


class StyleProfile(StrEnum):
    POSSESSION = "possession"
    HIGH_PRESS = "high_press"
    COUNTER = "counter"
    LOW_BLOCK = "low_block"
    DIRECT = "direct"


class Position(StrEnum):
    GK = "gk"
    CB = "cb"
    FB = "fb"
    DM = "dm"
    CM = "cm"
    AM = "am"
    WG = "wg"
    ST = "st"


class PreferredFoot(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    BOTH = "both"


class EventType(StrEnum):
    PASS = "pass"
    BALL_RECEIPT = "ball_receipt"
    CARRY = "carry"
    DRIBBLE = "dribble"
    SHOT = "shot"
    PRESSURE = "pressure"
    TACKLE = "tackle"
    INTERCEPTION = "interception"
    CLEARANCE = "clearance"
    BLOCK = "block"
    BALL_RECOVERY = "ball_recovery"
    GOALKEEPER = "goalkeeper"
    FOUL = "foul"
    CARD = "card"
    SUBSTITUTION = "substitution"
    OUT_OF_PLAY = "out_of_play"
    PERIOD_START = "period_start"
    PERIOD_END = "period_end"


class Outcome(StrEnum):
    """Every outcome value used by any event type; see ``ALLOWED_OUTCOMES``."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    OUT_OF_PLAY = "out_of_play"
    OFFSIDE = "offside"
    SUCCESS = "success"
    MISCONTROL = "miscontrol"
    DISPOSSESSED = "dispossessed"
    FAIL = "fail"
    GOAL = "goal"
    SAVED = "saved"
    OFF_TARGET = "off_target"
    BLOCKED = "blocked"
    POST = "post"
    FORCED_TURNOVER = "forced_turnover"
    NO_EFFECT = "no_effect"
    WON = "won"
    LOST = "lost"
    SAVE = "save"
    CATCH = "catch"
    PUNCH = "punch"
    CLAIM = "claim"
    FREE_KICK = "free_kick"
    PENALTY = "penalty"
    YELLOW = "yellow"
    SECOND_YELLOW = "second_yellow"
    RED = "red"
    THROW_IN = "throw_in"
    CORNER = "corner"
    GOAL_KICK = "goal_kick"


_O = Outcome
ALLOWED_OUTCOMES: dict[EventType, frozenset[Outcome]] = {
    EventType.PASS: frozenset({_O.COMPLETE, _O.INCOMPLETE, _O.OUT_OF_PLAY, _O.OFFSIDE}),
    EventType.BALL_RECEIPT: frozenset({_O.SUCCESS, _O.MISCONTROL}),
    EventType.CARRY: frozenset({_O.SUCCESS, _O.DISPOSSESSED}),
    EventType.DRIBBLE: frozenset({_O.SUCCESS, _O.FAIL}),
    EventType.SHOT: frozenset({_O.GOAL, _O.SAVED, _O.OFF_TARGET, _O.BLOCKED, _O.POST}),
    EventType.PRESSURE: frozenset({_O.FORCED_TURNOVER, _O.NO_EFFECT}),
    EventType.TACKLE: frozenset({_O.WON, _O.LOST}),
    EventType.INTERCEPTION: frozenset({_O.WON, _O.LOST}),
    EventType.CLEARANCE: frozenset({_O.SUCCESS, _O.FAIL}),
    EventType.BLOCK: frozenset({_O.SUCCESS}),
    EventType.BALL_RECOVERY: frozenset({_O.SUCCESS, _O.FAIL}),
    EventType.GOALKEEPER: frozenset({_O.SAVE, _O.CATCH, _O.PUNCH, _O.CLAIM, _O.FAIL}),
    EventType.FOUL: frozenset({_O.FREE_KICK, _O.PENALTY}),
    EventType.CARD: frozenset({_O.YELLOW, _O.SECOND_YELLOW, _O.RED}),
    EventType.SUBSTITUTION: frozenset({_O.SUCCESS}),
    EventType.OUT_OF_PLAY: frozenset({_O.THROW_IN, _O.CORNER, _O.GOAL_KICK}),
    EventType.PERIOD_START: frozenset(),
    EventType.PERIOD_END: frozenset(),
}
"""Allowed outcomes per event type. An empty set means the outcome must be null."""

NO_ACTOR_TYPES: frozenset[EventType] = frozenset(
    {EventType.OUT_OF_PLAY, EventType.PERIOD_START, EventType.PERIOD_END}
)
"""Event types with no acting team or player."""

NO_LOCATION_TYPES: frozenset[EventType] = NO_ACTOR_TYPES | {EventType.CARD, EventType.SUBSTITUTION}
"""Event types with no coordinates (no actor, or no movement)."""


class BodyPart(StrEnum):
    RIGHT_FOOT = "right_foot"
    LEFT_FOOT = "left_foot"
    HEAD = "head"
    OTHER = "other"


class PlayPattern(StrEnum):
    REGULAR_PLAY = "regular_play"
    FROM_KICKOFF = "from_kickoff"
    FROM_GOAL_KICK = "from_goal_kick"
    FROM_THROW_IN = "from_throw_in"
    FROM_CORNER = "from_corner"
    FROM_FREE_KICK = "from_free_kick"
    FROM_COUNTER = "from_counter"


class PassType(StrEnum):
    SHORT = "short"
    LONG = "long"
    CROSS = "cross"
    THROUGH_BALL = "through_ball"
    SWITCH = "switch"
    BACK_PASS = "back_pass"


class PassHeight(StrEnum):
    GROUND = "ground"
    LOW = "low"
    HIGH = "high"


class SetPiece(StrEnum):
    NONE = "none"
    CORNER = "corner"
    FREE_KICK = "free_kick"
    THROW_IN = "throw_in"
    GOAL_KICK = "goal_kick"
    KICK_OFF = "kick_off"


class ShotType(StrEnum):
    OPEN_PLAY = "open_play"
    PENALTY = "penalty"
    FREE_KICK = "free_kick"
    HEADER = "header"


class CardReason(StrEnum):
    FOUL = "foul"
    DISSENT = "dissent"
    TIME_WASTING = "time_wasting"
    SECOND_YELLOW = "second_yellow"


class GoalkeeperAction(StrEnum):
    DIVE_SAVE = "dive_save"
    STANDING_SAVE = "standing_save"
    CATCH = "catch"
    PUNCH = "punch"
    CLAIM = "claim"


class InsightType(StrEnum):
    MOMENTUM_SHIFT = "momentum_shift"
    KEY_MOMENT = "key_moment"
    MILESTONE = "milestone"
    SPEED_ALERT = "speed_alert"
    PASS_QUALITY = "pass_quality"
    PLAYER_FORM = "player_form"
    RECAP = "recap"


class AuditStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CORRECTED = "corrected"
    BLOCKED = "blocked"


class OverlayTemplate(StrEnum):
    PLAYER_TAG = "player_tag"
    SPEED_BADGE = "speed_badge"
    STAT_CARD = "stat_card"
    MOMENTUM_BAR = "momentum_bar"
    LOWER_THIRD = "lower_third"
    RECAP_CARD = "recap_card"
    MILESTONE_BADGE = "milestone_badge"
    PASS_BADGE = "pass_badge"


class AudienceMode(StrEnum):
    ANALYST = "analyst"
    CASUAL = "casual"
    CLUB_FOCUS = "club_focus"
    PLAYER_FOCUS = "player_focus"
    SINGLE_METRIC = "single_metric"


class ScenarioId(StrEnum):
    LATE_COLLAPSE = "late_collapse"
    HIGH_PRESS_SWITCH = "high_press_switch"
    RED_CARD = "red_card"
    HOT_STREAK = "hot_streak"
    NONE = "none"


class EffectOp(StrEnum):
    """How a scenario effect modifies a generator parameter."""

    ADD = "add"
    MUL = "mul"
