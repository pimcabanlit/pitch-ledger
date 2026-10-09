"""In-play time and turnovers derived from the event stream.

- Play is live from a restart (kick-off, set-piece pass, penalty or direct free-kick
  shot) until a stoppage (ball out of play, foul, offside, goal, period end). Live time
  between two events belongs to the possession team of the earlier event.
- A turnover is a possession lost by an incomplete pass (including out of play and
  offside), a miscontrol, a dispossession, a failed dribble, or a lost duel (the
  opponent wins a tackle or interception). Its location is in the losing team's frame.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from pitch_ledger.core.pitch import PITCH_LENGTH_M, flip
from pitch_ledger.schema import Event, PassDetails, ShotDetails
from pitch_ledger.schema.enums import EventType, Outcome, SetPiece, ShotType

ON_BALL_TYPES = frozenset(
    {
        EventType.PASS,
        EventType.BALL_RECEIPT,
        EventType.CARRY,
        EventType.DRIBBLE,
        EventType.SHOT,
        EventType.BALL_RECOVERY,
    }
)
FAILED_PASS = frozenset({Outcome.INCOMPLETE, Outcome.OUT_OF_PLAY, Outcome.OFFSIDE})
HALFWAY_X = PITCH_LENGTH_M / 2


@dataclass(frozen=True)
class PlaySpan:
    """Live ball time in one period, ``start_ms`` to ``end_ms``, owned by ``team_id``."""

    period: int
    start_ms: int
    end_ms: int
    team_id: str


@dataclass(frozen=True)
class Turnover:
    """A possession lost by ``team_id``; ``event_id`` is the evidence event.

    ``clock_ms`` is when the cause happened; ``detected_ms`` is when the opponent took
    possession, the earliest moment a live engine can count it.
    """

    event_id: str
    team_id: str
    period: int
    clock_ms: int
    detected_ms: int
    x: float
    y: float
    cause: str

    @property
    def own_half(self) -> bool:
        return self.x < HALFWAY_X


def is_restart(event: Event) -> bool:
    """True for events that put the ball back in play."""
    if isinstance(event.details, PassDetails):
        return event.details.set_piece is not SetPiece.NONE
    if isinstance(event.details, ShotDetails):
        return event.details.shot_type in (ShotType.PENALTY, ShotType.FREE_KICK)
    return False


def stops_play(event: Event) -> bool:
    """True for events after which the ball is dead until the next restart."""
    if event.event_type in (EventType.OUT_OF_PLAY, EventType.FOUL, EventType.PERIOD_END):
        return True
    if event.event_type is EventType.SHOT and event.outcome is Outcome.GOAL:
        return True
    return event.event_type is EventType.PASS and event.outcome is Outcome.OFFSIDE


def play_spans(events: Sequence[Event], until_ms: dict[int, int] | None = None) -> list[PlaySpan]:
    """Live-ball spans per period, in order. The last span of a period stays open until
    the next event, or ``until_ms[period]`` when the stream has not reached it yet."""
    spans: list[PlaySpan] = []
    live = False
    for i, event in enumerate(events):
        if event.event_type is EventType.PERIOD_START:
            live = False
        if is_restart(event):
            live = True
        if stops_play(event):
            live = False
            continue
        if not live:
            continue
        nxt = events[i + 1] if i + 1 < len(events) else None
        if nxt is not None and nxt.period == event.period:
            end = nxt.clock_ms
        elif until_ms is not None and event.period in until_ms:
            end = until_ms[event.period]
        else:
            continue
        if end > event.clock_ms:
            spans.append(PlaySpan(event.period, event.clock_ms, end, event.possession_team_id))
    return spans


def _failed(event: Event) -> str | None:
    if event.event_type is EventType.PASS and event.outcome in FAILED_PASS:
        return f"pass_{event.outcome.value}" if event.outcome else None
    if event.event_type is EventType.BALL_RECEIPT and event.outcome is Outcome.MISCONTROL:
        return "miscontrol"
    if event.event_type is EventType.CARRY and event.outcome is Outcome.DISPOSSESSED:
        return "dispossessed"
    if event.event_type is EventType.DRIBBLE and event.outcome is Outcome.FAIL:
        return "dribble_fail"
    return None


def turnovers(events: Sequence[Event]) -> list[Turnover]:
    """Every turnover in the stream, attributed to the event that caused it."""
    out: list[Turnover] = []
    last_ball: dict[str, Event | None] = {}
    prev: Event | None = None
    for event in events:
        if event.event_type is EventType.PERIOD_START:
            last_ball.clear()
            prev = event
            continue
        if (
            prev is not None
            and prev.period == event.period
            and event.possession_team_id != prev.possession_team_id
        ):
            lost = prev.possession_team_id
            cause = last_ball.get(lost)
            reason = _failed(cause) if cause is not None else None
            if cause is not None and reason is not None:
                assert cause.start_x is not None and cause.start_y is not None
                out.append(
                    Turnover(
                        cause.event_id,
                        lost,
                        cause.period,
                        cause.clock_ms,
                        event.clock_ms,
                        cause.start_x,
                        cause.start_y,
                        reason,
                    )
                )
            elif (
                event.event_type in (EventType.TACKLE, EventType.INTERCEPTION)
                and event.outcome is Outcome.WON
                and event.start_x is not None
                and event.start_y is not None
            ):
                x, y = flip(event.start_x, event.start_y)
                out.append(
                    Turnover(
                        event.event_id,
                        lost,
                        event.period,
                        event.clock_ms,
                        event.clock_ms,
                        x,
                        y,
                        f"lost_{event.event_type.value}",
                    )
                )
            last_ball[event.possession_team_id] = None
        if event.team_id is not None and event.event_type in ON_BALL_TYPES:
            last_ball[event.team_id] = event
        prev = event
    return out
