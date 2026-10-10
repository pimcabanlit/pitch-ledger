"""1 Hz player tracking consistent with the event stream.

Each second, every player on the pitch moves toward a target: their formation slot,
shifted by the ball and by which team has it (``matchlab.shape``), plus a slowly drifting
personal offset. A player about to act in an event plans their run to that event's location
so they arrive on time: they commit when the time left matches a jog there (or the last
couple of seconds), up to their top speed. While the ball is dead before a restart, players
walk into the restart's shape (``matchlab.set_pieces``): a kick-off, a penalty, a corner,
a free kick, a goal kick or a throw-in. Short sprint bursts happen every couple of minutes.
Steps are capped at the player's top speed, and ``speed_kmh`` is the distance moved since
the previous second, so speeds and distance covered always agree.

Positions are stored in the player's own team frame (it attacks toward x = 105).
"""

import bisect
import itertools
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from pitch_ledger.core.pitch import PITCH_LENGTH_M, PITCH_WIDTH_M, flip
from pitch_ledger.matchlab.generator import SimulatedMatch
from pitch_ledger.matchlab.set_pieces import (
    KICK_OFF,
    PENALTY,
    Restart,
    enforce_laws,
    set_piece_position,
)
from pitch_ledger.matchlab.shape import slot_position
from pitch_ledger.matchlab.styles import ModifierProvider, no_modifiers
from pitch_ledger.schema import (
    Event,
    PassDetails,
    ShotDetails,
    SubstitutionDetails,
    Team,
    TrackingSample,
)
from pitch_ledger.schema.enums import EventType, Outcome, SetPiece, ShotType

LOOKAHEAD_S = 8
"""The furthest ahead a player plans a run to an event location (seconds)."""
COMMIT_S = 2
"""A player always heads for their event location in its last seconds."""
EASY_SHARE = 0.6
"""Share of top speed of an unhurried run to an event location (a jog)."""
RUN_SHARE = 0.97
"""Share of top speed when a player has to hurry to reach an event location."""
SETTLE_MS = 2000
"""After play stops, players start moving into the restart's shape this much later."""
DEAD_BALL_REACTION = 0.35
"""Players settle into a set-piece shape faster than they follow the ball in play."""
DEAD_BALL_DRIFT = 0.8
"""Personal drift is damped while players hold a set-piece shape."""

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


def _anchors(events: Sequence[Event]) -> dict[str, list[tuple[float, float, float]]]:
    """Per player, (time in seconds, x, y) points where they act, in their own frame."""
    out: dict[str, list[tuple[float, float, float]]] = {}
    for e in events:
        if e.player_id is None or e.start_x is None or e.start_y is None:
            continue
        points = out.setdefault(e.player_id, [])
        points.append((e.clock_ms / 1000, e.start_x, e.start_y))
        if e.event_type is EventType.CARRY and e.duration_ms and e.end_x is not None:
            end_s = (e.clock_ms + e.duration_ms) / 1000
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
        restarts = _restarts(events)
        restart_i = 0
        kick_off = next((r for r in restarts if r.kind == KICK_OFF), None)
        for state in on_pitch.values():  # each half starts lined up for the kick-off
            if kick_off is not None:
                team = teams[state.team_id]
                state.x, state.y = set_piece_position(
                    kick_off, state.team_id, team.formation, state.slot, team.style_profile
                )
            state.sx, state.sy = state.x, state.y
            if period == 2:
                state.next_sprint = rng.randint(10, SPRINT_GAP_S[1])

        for second in range(end_ms // 1000 + 1):
            t_ms = second * 1000
            while change_i < len(changes) and changes[change_i].clock_ms <= t_ms:
                _apply_change(changes[change_i], on_pitch, second + rng.randint(*SPRINT_GAP_S))
                change_i += 1
            bx, by, poss = ball.at(t_ms)
            while restart_i < len(restarts) and restarts[restart_i].at_ms + 500 < t_ms:
                restart_i += 1
            dead = None
            if restart_i < len(restarts):
                r = restarts[restart_i]
                if r.kind != KICK_OFF or r.stop_ms < r.at_ms:  # not the kick-off of a half
                    dead = r if r.stop_ms + SETTLE_MS <= t_ms else None
            for pid, st in on_pitch.items():
                team = teams[st.team_id]
                own_ball = (bx, by) if st.team_id == home else flip(bx, by)
                in_possession = poss == st.team_id
                mods = modifiers(st.team_id, period, t_ms)
                shift = 0.0 if in_possession else mods.block_shift_m
                if dead is not None:
                    shape_x, shape_y = set_piece_position(
                        dead, st.team_id, team.formation, st.slot, team.style_profile
                    )
                else:
                    shape_x, shape_y = slot_position(
                        team.formation, st.slot, *own_ball, in_possession, team.style_profile, shift
                    )
                if st.sx is None or st.sy is None:
                    st.sx, st.sy = shape_x, shape_y
                react = DEAD_BALL_REACTION if dead is not None else REACTION
                st.sx += react * (shape_x - st.sx)
                st.sy += react * (shape_y - st.sy)
                tx, ty = st.sx, st.sy
                st.dx += -DRIFT_THETA * st.dx + rng.gauss(0, DRIFT_SIGMA)
                st.dy += -DRIFT_THETA * st.dy + rng.gauss(0, DRIFT_SIGMA)
                drift = DEAD_BALL_DRIFT if dead is not None else 1.0
                tx, ty = tx + st.dx * drift, ty + st.dy * drift
                if dead is not None:
                    tx, ty = enforce_laws(dead, st.team_id, st.slot, pid, (tx, ty))
                cap_kmh = top_speed[pid] * mods.top_speed_mult
                limit = cap_kmh * 0.62
                # Opponents of the restarting side hold their positions until the ball is played.
                wait = dead is not None and st.team_id != dead.team_id
                until = dead.at_ms / 1000 if dead is not None and wait else None
                run = _planned_run(anchors.get(pid), cursor, pid, second, st, cap_kmh, until)
                if run is not None:
                    tx, ty, limit = run
                elif st.slot > 0 and dead is None:
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
                if second == 0:
                    frac = 0.0  # the whistle: everyone still where they lined up
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


def _planned_run(
    points: list[tuple[float, float, float]] | None,
    cursor: dict[str, int],
    pid: str,
    second: int,
    st: _PlayerState,
    cap_kmh: float,
    until_s: float | None = None,
) -> tuple[float, float, float] | None:
    """Target and speed limit (km/h) of a run to ``pid``'s next event location, if committed.

    The run is timed to arrive at the sample nearest the event: the player commits once the
    time left matches a jog there (or in the last ``COMMIT_S`` seconds) and then covers an
    equal share of the gap each second, hurrying up to ``RUN_SHARE`` of top speed if needed.
    Events after ``until_s`` wait (a defender does not move in before a restart is taken).
    """
    if not points:
        return None
    i = cursor[pid]
    while i < len(points) and round(points[i][0]) < second:
        i += 1
    cursor[pid] = i
    if i == len(points):
        return None
    if until_s is not None and points[i][0] > until_s:
        return None
    due, ax, ay = round(points[i][0]), points[i][1], points[i][2]
    steps = due - second + 1  # samples left, this one included
    if steps > LOOKAHEAD_S + 1:
        return None
    gap = math.hypot(ax - st.x, ay - st.y)
    if steps > COMMIT_S and gap < cap_kmh * EASY_SHARE / 3.6 * (steps - 1):
        return None  # time to spare: keep the team shape a little longer
    return ax, ay, min(cap_kmh * RUN_SHARE, gap / steps * 3.6)


def _restarts(events: Sequence[Event]) -> list[Restart]:
    """Dead-ball spells of one period: from the event before a restart to the restart."""
    out: list[Restart] = []
    for prev, e in itertools.pairwise(events):
        kind = None
        if isinstance(e.details, PassDetails) and e.details.set_piece is not SetPiece.NONE:
            kind = e.details.set_piece.value
        elif isinstance(e.details, ShotDetails) and e.details.shot_type is not ShotType.OPEN_PLAY:
            kind = PENALTY if e.details.shot_type is ShotType.PENALTY else "free_kick"
        if kind is None or e.team_id is None or e.start_x is None or e.start_y is None:
            continue
        ball = (e.start_x, e.start_y)
        out.append(Restart(kind, e.team_id, prev.clock_ms, e.clock_ms, ball, e.player_id))
    return out


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
