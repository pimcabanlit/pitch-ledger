"""Where players stand while the ball is dead, waiting for a restart (tracking only).

Each shape is laid out in the restarting team's frame (it attacks toward x = 105) and
returned in the player's own frame, so defenders' spots are mirrored. Positions follow the
Laws of the Game: at a kick-off everyone is in their own half (opponents outside the centre
circle); at a penalty only the taker and the goalkeeper (on the goal line) are in the box
and arc; at a goal kick opponents are outside the penalty area; at free kicks and corners
opponents are 9.15 m from the ball. The event generator never reads these: events are
unchanged, only the dead-ball time in tracking gets a realistic shape.
"""

import math
from dataclasses import dataclass

from pitch_ledger.core.pitch import PITCH_LENGTH_M, PITCH_WIDTH_M, flip
from pitch_ledger.matchlab.shape import FORMATION_SLOTS, Point, slot_position
from pitch_ledger.schema.enums import StyleProfile

KICK_OFF = "kick_off"
PENALTY = "penalty"
CORNER = "corner"
FREE_KICK = "free_kick"
GOAL_KICK = "goal_kick"
THROW_IN = "throw_in"

BOX_X = PITCH_LENGTH_M - 16.5  # attacking penalty area starts here (restarting team's frame)
BOX_Y = (13.84, 54.16)
SPOT = (PITCH_LENGTH_M - 11.0, 34.0)
DISTANCE_M = 9.15  # opponents' distance from the ball at a restart
DANGEROUS_X = 72.0  # a free kick from here on is played into the box (a wall forms)

# Spots in and around the box for a ball delivered from a corner or a wide free kick.
ATTACK_SPOTS: list[Point] = [(97, 31), (99, 37), (94, 35), (100, 33), (92, 40), (89, 28), (85, 34)]
MARK_SPOTS: list[Point] = [
    (99.5, 30), (99.5, 38), (101, 34), (96, 32), (96, 37), (93, 29), (93, 40), (90, 34), (98, 26),
]  # fmt: skip


@dataclass(frozen=True)
class Restart:
    """A dead-ball spell: play stopped at ``stop_ms`` and restarts at ``at_ms``."""

    kind: str
    team_id: str  # the restarting team
    stop_ms: int
    at_ms: int
    ball: Point  # restart spot, in the restarting team's frame
    taker: str | None = None


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def _back_line(formation: str, slot: int) -> bool:
    return slot > 0 and FORMATION_SLOTS[formation][slot][0] < 40


def _rank(formation: str, slot: int, members: list[int]) -> int:
    return members.index(slot) if slot in members else 0


def keep_distance(p: Point, ball: Point, radius: float = DISTANCE_M) -> Point:
    """Push a spot out to ``radius`` from the ball (along the line from the ball)."""
    dx, dy = p[0] - ball[0], p[1] - ball[1]
    d = math.hypot(dx, dy)
    if d >= radius:
        return p
    if d < 0.1:
        dx, dy, d = -1.0, 0.0, 1.0  # straight back toward their own goal
    return ball[0] + dx / d * radius, ball[1] + dy / d * radius


def _penalty(formation: str, slot: int, taking: bool, style: StyleProfile) -> Point:
    """Restarting frame. Rest defence stays back; everyone else waits on the edge of the box."""
    if taking and slot == 0:
        return slot_position(formation, 0, *SPOT, True, style)
    if not taking and slot == 0:
        return PITCH_LENGTH_M - 0.3, 34.0  # on the goal line
    if taking and _back_line(formation, slot):
        bx, by = FORMATION_SLOTS[formation][slot]
        return 50 + (bx - 26) * 0.5, by
    if taking:
        members = [s for s in range(1, 11) if not _back_line(formation, s)]
        k = _rank(formation, slot, members)
        y = 34 + (k - (len(members) - 1) / 2) * 5.0
        x = 85.5
    else:
        y = 13.0 + (slot - 1) * 4.6
        x = 88.0
    if abs(y - 34) < 8.5:  # stay outside the arc as well as the box
        x -= 4.5
    return x, y


def _box_delivery(
    formation: str, slot: int, taking: bool, ball: Point, style: StyleProfile
) -> Point:
    """Corner or a free kick into the box, in the restarting frame."""
    by = FORMATION_SLOTS[formation][slot][1]
    if taking:
        if slot == 0:
            return slot_position(formation, 0, *ball, True, style)
        if _back_line(formation, slot):
            wide = by < 15 or by > 53
            return (68.0 if wide else 55.0), by
        members = [s for s in range(1, 11) if not _back_line(formation, s)]
        return ATTACK_SPOTS[_rank(formation, slot, members) % len(ATTACK_SPOTS)]
    if slot == 0:
        return PITCH_LENGTH_M - 0.7, _clamp(34 + (ball[1] - 34) * 0.15, 30, 38)
    striker = max(range(1, 11), key=lambda s: (FORMATION_SLOTS[formation][s][0], -s))
    if slot == striker:
        return 60.0, 34.0  # one forward stays up for the counter
    if ball[0] < PITCH_LENGTH_M - 1:  # a free kick: four in a wall between ball and goal
        members = [s for s in range(1, 11) if s != striker]
        k = _rank(formation, slot, members)
        if k < 4:
            gx, gy = PITCH_LENGTH_M, 34.0
            d = math.hypot(gx - ball[0], gy - ball[1]) or 1.0
            ux, uy = (gx - ball[0]) / d, (gy - ball[1]) / d
            off = (k - 1.5) * 1.1
            return ball[0] + ux * DISTANCE_M - uy * off, ball[1] + uy * DISTANCE_M + ux * off
        return keep_distance(MARK_SPOTS[(k - 4) % len(MARK_SPOTS)], ball)
    members = [s for s in range(1, 11) if s != striker]
    return keep_distance(MARK_SPOTS[_rank(formation, slot, members) % len(MARK_SPOTS)], ball)


def set_piece_position(
    restart: Restart, team_id: str, formation: str, slot: int, style: StyleProfile
) -> Point:
    """Where ``slot`` of ``team_id`` waits for ``restart``, in that team's own frame."""
    taking = team_id == restart.team_id
    ball = restart.ball
    kind = restart.kind
    if kind == KICK_OFF:
        bx, by = FORMATION_SLOTS[formation][slot]
        if slot == 0:
            return 6.0, 34.0
        x = min(18 + bx * 0.42, 49.5 if taking else PITCH_LENGTH_M / 2 - DISTANCE_M - 1.0)
        return round(x, 1), by
    if kind == PENALTY:
        p = _penalty(formation, slot, taking, style)
    elif kind == CORNER or (kind == FREE_KICK and ball[0] >= DANGEROUS_X):
        p = _box_delivery(formation, slot, taking, ball, style)
    else:
        # Throw-in, goal kick or a free kick away from goal: the usual shape around the ball.
        own_ball = ball if taking else flip(*ball)
        p = slot_position(formation, slot, *own_ball, taking, style)
        if not taking:
            p = flip(*p)  # back to the restarting frame for the distance rules
            if kind == GOAL_KICK and p[0] < 16.5 + 1 and BOX_Y[0] - 1 < p[1] < BOX_Y[1] + 1:
                p = (17.5, p[1])
            elif kind == FREE_KICK and slot > 0:
                p = keep_distance(p, ball)
    x, y = p
    x = _clamp(x, 0.3, PITCH_LENGTH_M - 0.3)
    y = _clamp(y, 1.0, PITCH_WIDTH_M - 1.0)
    out = (x, y) if taking else flip(x, y)
    return round(out[0], 1), round(out[1], 1)


def enforce_laws(restart: Restart, team_id: str, slot: int, pid: str, p: Point) -> Point:
    """Hold a moving player (own frame) to the restart's positioning rules.

    Applied after personal drift, so a wandering player never breaks the Laws: opponents keep
    9.15 m at free kicks and corners and stay out of the area at goal kicks; at a penalty
    everyone but the taker stays out of the area and arc and the goalkeeper keeps to the
    line; at a kick-off everyone is in their own half, opponents outside the centre circle.
    """
    if pid == restart.taker or restart.kind == THROW_IN:
        return p
    taking = team_id == restart.team_id
    x, y = p if taking else flip(*p)  # restarting frame
    if restart.kind == KICK_OFF:
        own_x = x if taking else PITCH_LENGTH_M - x
        own_x = min(own_x, PITCH_LENGTH_M / 2 - 0.5)
        x = own_x if taking else PITCH_LENGTH_M - own_x
        if not taking:
            x, y = keep_distance((x, y), (PITCH_LENGTH_M / 2, PITCH_WIDTH_M / 2), DISTANCE_M + 0.5)
    elif restart.kind == PENALTY:
        if not taking and slot == 0:
            x, y = PITCH_LENGTH_M - 0.3, _clamp(y, 30.5, 37.5)  # on the line, between the posts
        elif slot > 0 or taking:
            if x > BOX_X - 1.0 and BOX_Y[0] - 1.0 < y < BOX_Y[1] + 1.0:
                x = BOX_X - 1.0
            x, y = keep_distance((x, y), SPOT, DISTANCE_M + 0.5)
    elif not taking:
        if restart.kind == GOAL_KICK:
            if x < 16.5 + 1.0 and BOX_Y[0] - 1.0 < y < BOX_Y[1] + 1.0:
                x = 16.5 + 1.0
        else:  # free kick or corner
            x, y = keep_distance((x, y), restart.ball)
    x = _clamp(x, 0.3, PITCH_LENGTH_M - 0.3)
    y = _clamp(y, 0.5, PITCH_WIDTH_M - 0.5)
    return (x, y) if taking else flip(x, y)
