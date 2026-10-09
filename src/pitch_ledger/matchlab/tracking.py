"""1 Hz player tracking consistent with the event stream.

Each second, every player on the pitch moves toward a target: their formation slot,
shifted by the ball and by which team has it (``matchlab.shape``), plus a slowly drifting
personal offset. A player about to act in an event heads for that event's location.
Short sprint bursts happen every couple of minutes. Steps are capped at the player's
top speed, and ``speed_kmh`` is the distance moved since the previous second, so speeds
and distance covered always agree.

Positions are stored in the player's own team frame (it attacks toward x = 105).
"""

import bisect
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from pitch_ledger.core.pitch import PITCH_LENGTH_M, PITCH_WIDTH_M, flip
from pitch_ledger.matchlab.generator import SimulatedMatch
from pitch_ledger.matchlab.shape import slot_position
from pitch_ledger.matchlab.styles import ModifierProvider, no_modifiers
from pitch_ledger.schema import Event, SubstitutionDetails, Team, TrackingSample
from pitch_ledger.schema.enums import EventType, Outcome

LOOKAHEAD_S = 3
"""How many seconds ahead a player starts moving toward an event location."""

DRIFT_THETA = 0.1
DRIFT_SIGMA = 1.0
REACTION = 0.18
"""Share of the gap to the shape target a player closes each second (smooths ball chasing)."""
SPRINT_GAP_S = (130, 280)
SPRINT_LEN_S = (2, 4)


@dataclass
class _PlayerState:
    team_id: str
    slot: int
    x: float
    y: float
    dx: float = 0.0
    dy: float = 0.0
    sx: float | None = None
    sy: float | None = None
    sprint_left: int = 0
    sprint_dir: tuple[float, float] = (1.0, 0.0)
    next_sprint: int = 0


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


class _BallTimeline:
    """Ball position (home frame) and possession team at any moment of a period."""

    def __init__(self, events: Sequence[Event], home: str) -> None:
        self.times: list[int] = []
        self.states: list[tuple[float, float, float, float, int, str]] = []
        x, y = 52.5, 34.0
        for e in events:
            if e.start_x is not None and e.start_y is not None:
                sx, sy = e.start_x, e.start_y
                ex, ey = e.end_x or sx, e.end_y or sy
                if e.team_id != home:
                    sx, sy = flip(sx, sy)
                    ex, ey = flip(ex, ey)
                x, y = ex, ey
            else:
                sx = ex = x
                sy = ey = y
            self.times.append(e.clock_ms)
            self.states.append((sx, sy, ex, ey, e.duration_ms or 0, e.possession_team_id))

    def at(self, t_ms: int) -> tuple[float, float, str]:
        i = bisect.bisect_right(self.times, t_ms) - 1
        if i < 0:
            return 52.5, 34.0, self.states[0][5] if self.states else ""
        sx, sy, ex, ey, dur, team = self.states[i]
        elapsed = t_ms - self.times[i]
        if dur and elapsed < dur:
            f = elapsed / dur
            return sx + f * (ex - sx), sy + f * (ey - sy), team
        return ex, ey, team


def _anchors(events: Sequence[Event]) -> dict[str, list[tuple[int, float, float]]]:
    """Per player, (second, x, y) points where they act, in their own frame."""
    out: dict[str, list[tuple[int, float, float]]] = {}
    for e in events:
        if e.player_id is None or e.start_x is None or e.start_y is None:
            continue
        points = out.setdefault(e.player_id, [])
        points.append((e.clock_ms // 1000, e.start_x, e.start_y))
        if e.event_type is EventType.CARRY and e.duration_ms and e.end_x is not None:
            end_s = (e.clock_ms + e.duration_ms) // 1000
            points.append((end_s, e.end_x, e.end_y if e.end_y is not None else e.start_y))
    for points in out.values():
        points.sort()
    return out


def generate_tracking(
    sim: SimulatedMatch, modifiers: ModifierProvider = no_modifiers
) -> list[TrackingSample]:
    """Build the ``player_tracking`` table for a simulated match."""
    match = sim.match
    rng = random.Random(f"{match.random_seed}:tracking")
    teams: dict[str, Team] = {t.team_id: t for t in sim.teams}
    top_speed = {p.player_id: p.top_speed_kmh for p in sim.players}
    starters = {
        tid: [p.player_id for p in sim.players if p.team_id == tid and p.is_starter]
        for tid in teams
    }
    home = match.home_team_id
    on_pitch: dict[str, _PlayerState] = {}
    for tid, ids in starters.items():
        team = teams[tid]
        for slot, pid in enumerate(ids):
            x, y = slot_position(team.formation, slot, 52.5, 34.0, False, team.style_profile)
            first_sprint = rng.randint(10, SPRINT_GAP_S[1])
            on_pitch[pid] = _PlayerState(tid, slot, x, y, next_sprint=first_sprint)

    samples: list[TrackingSample] = []
    for period in (1, 2):
        events = [e for e in sim.events if e.period == period]
        end_ms = next(e.clock_ms for e in events if e.event_type is EventType.PERIOD_END)
        ball = _BallTimeline(events, home)
        anchors = _anchors(events)
        cursor = {pid: 0 for pid in anchors}
        changes = [e for e in events if e.event_type in (EventType.SUBSTITUTION, EventType.CARD)]
        change_i = 0
        if period == 2:
            for state in on_pitch.values():  # teams return to shape after half-time
                state.next_sprint = rng.randint(10, SPRINT_GAP_S[1])

        for second in range(end_ms // 1000 + 1):
            t_ms = second * 1000
            while change_i < len(changes) and changes[change_i].clock_ms <= t_ms:
                _apply_change(changes[change_i], on_pitch, second + rng.randint(*SPRINT_GAP_S))
                change_i += 1
            bx, by, poss = ball.at(t_ms)
            for pid, st in on_pitch.items():
                team = teams[st.team_id]
                own_ball = (bx, by) if st.team_id == home else flip(bx, by)
                in_possession = poss == st.team_id
                mods = modifiers(st.team_id, period, t_ms)
                shift = 0.0 if in_possession else mods.block_shift_m
                shape_x, shape_y = slot_position(
                    team.formation, st.slot, *own_ball, in_possession, team.style_profile, shift
                )
                if st.sx is None or st.sy is None:
                    st.sx, st.sy = shape_x, shape_y
                st.sx += REACTION * (shape_x - st.sx)
                st.sy += REACTION * (shape_y - st.sy)
                tx, ty = st.sx, st.sy
                st.dx += -DRIFT_THETA * st.dx + rng.gauss(0, DRIFT_SIGMA)
                st.dy += -DRIFT_THETA * st.dy + rng.gauss(0, DRIFT_SIGMA)
                tx, ty = tx + st.dx, ty + st.dy
                cap_kmh = top_speed[pid] * mods.top_speed_mult
                limit = cap_kmh * 0.62
                anchored = _next_anchor(anchors.get(pid), cursor, pid, second)
                if anchored is not None:
                    tx, ty = anchored
                    limit = cap_kmh * 0.75
                elif st.slot > 0:
                    if second >= st.next_sprint and st.sprint_left == 0:
                        st.sprint_left = rng.randint(*SPRINT_LEN_S)
                        angle = rng.uniform(-0.6, 0.6) + (0.0 if poss == st.team_id else math.pi)
                        st.sprint_dir = (math.cos(angle), math.sin(angle))
                        st.next_sprint = second + rng.randint(*SPRINT_GAP_S)
                    if st.sprint_left > 0:
                        st.sprint_left -= 1
                        burst = cap_kmh / 3.6 * rng.uniform(0.86, 0.99)
                        tx = st.x + st.sprint_dir[0] * burst
                        ty = st.y + st.sprint_dir[1] * burst
                        limit = cap_kmh * 0.99
                        if not (2.0 < tx < PITCH_LENGTH_M - 2.0 and 2.0 < ty < PITCH_WIDTH_M - 2):
                            st.sprint_dir = (-st.sprint_dir[0], -st.sprint_dir[1])
                            tx = st.x + st.sprint_dir[0] * burst
                            ty = st.y + st.sprint_dir[1] * burst
                step_max = limit / 3.6
                gap = math.hypot(tx - st.x, ty - st.y)
                frac = 1.0 if gap <= step_max else step_max / gap
                nx = round(_clamp(st.x + (tx - st.x) * frac, 0.0, PITCH_LENGTH_M), 1)
                ny = round(_clamp(st.y + (ty - st.y) * frac, 0.0, PITCH_WIDTH_M), 1)
                speed = 0.0 if second == 0 else round(math.hypot(nx - st.x, ny - st.y) * 3.6, 1)
                st.x, st.y = nx, ny
                samples.append(
                    TrackingSample(
                        match_id=match.match_id,
                        period=period,
                        clock_ms=t_ms,
                        player_id=pid,
                        x=nx,
                        y=ny,
                        speed_kmh=speed,
                    )
                )
    return samples


def _next_anchor(
    points: list[tuple[int, float, float]] | None,
    cursor: dict[str, int],
    pid: str,
    second: int,
) -> tuple[float, float] | None:
    """The next event location for ``pid`` if it is within the look-ahead window."""
    if not points:
        return None
    i = cursor[pid]
    while i < len(points) and points[i][0] < second:
        i += 1
    cursor[pid] = i
    if i < len(points) and points[i][0] - second <= LOOKAHEAD_S:
        return points[i][1], points[i][2]
    return None


def _apply_change(event: Event, on_pitch: dict[str, _PlayerState], first_sprint: int) -> None:
    """Substitutions swap a player into the same slot; sending-offs remove one."""
    if isinstance(event.details, SubstitutionDetails):
        out = on_pitch.pop(event.details.player_out_id, None)
        if out is not None:
            on_pitch[event.details.player_in_id] = _PlayerState(
                out.team_id, out.slot, 52.5, 0.5, next_sprint=first_sprint
            )
    elif event.outcome in (Outcome.RED, Outcome.SECOND_YELLOW) and event.player_id:
        on_pitch.pop(event.player_id, None)
