"""Seeded, possession-based match simulator.

One ``random.Random(seed)`` drives every choice, so a seed regenerates the match exactly.
The simulation walks from situation to situation (a player on the ball, a loose ball, a
dead-ball restart) and emits events as it goes:

- pass outcomes use the pass-difficulty model and shot outcomes the xG model
  (``pitch_ledger.core.formulas``); style and ratings only change choices;
- coordinates are in the acting team's frame (it attacks toward x = 105), so a
  defender's event at the ball is stored flipped;
- possession changes only when a team gains control (won tackle or interception,
  ball recovery, goalkeeper catch, restart), never on a defensive touch.
"""

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pitch_ledger.core.formulas import (
    distance,
    expected_goals,
    forward_progress,
    pass_completion_probability,
)
from pitch_ledger.core.pitch import (
    CENTRE_SPOT,
    PENALTY_SPOT_X,
    PITCH_LENGTH_M,
    PITCH_WIDTH_M,
    POST_HIGH_Y,
    POST_LOW_Y,
    flip,
    in_attacking_box,
)
from pitch_ledger.matchlab.roster import Club, load_league
from pitch_ledger.matchlab.shape import slot_position
from pitch_ledger.matchlab.styles import (
    STYLES,
    ModifierProvider,
    Modifiers,
    StyleParams,
    no_modifiers,
)
from pitch_ledger.schema import (
    CardDetails,
    DuelDetails,
    Event,
    EventDetails,
    FoulDetails,
    GoalkeeperDetails,
    Match,
    NoDetails,
    PassDetails,
    Player,
    PressureDetails,
    ShotDetails,
    SubstitutionDetails,
    Team,
)
from pitch_ledger.schema.enums import (
    BodyPart,
    CardReason,
    EventType,
    GoalkeeperAction,
    Outcome,
    PassHeight,
    PassType,
    PlayPattern,
    Position,
    PreferredFoot,
    SetPiece,
    ShotType,
)
from pitch_ledger.schema.types import event_id

GENERATOR_VERSION = "0.1.0"
PERIOD_MS = 45 * 60 * 1000
HALF_TIME_BREAK_MS = 15 * 60 * 1000
DEFAULT_KICKOFF = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)
MAX_SUBS = 5

Point = tuple[float, float]


@dataclass(frozen=True)
class MatchSpec:
    """Everything needed to regenerate a match exactly."""

    match_id: str = "M0001"
    home_team_id: str = "T01"
    away_team_id: str = "T02"
    seed: int = 4211
    kickoff_utc: datetime = DEFAULT_KICKOFF
    scenario_id: str = "none"


@dataclass(frozen=True)
class SimulatedMatch:
    """Reference rows and the event stream (tracking is added by ``matchlab.tracking``)."""

    match: Match
    teams: list[Team]
    players: list[Player]
    events: list[Event]


# Situations ---------------------------------------------------------------
@dataclass
class _Carrier:
    """A player in control of the ball at (x, y) in their team's frame."""

    team: str
    player: str
    x: float
    y: float
    pressed: bool
    last: Event
    assist: Event | None = None
    counter: bool = False
    carries: int = 0


@dataclass
class _Loose:
    """A loose ball at (x, y) in ``team``'s frame; ``keep`` is the chance ``team`` wins it."""

    team: str
    x: float
    y: float
    keep: float
    related: Event


@dataclass
class _Restart:
    """A dead-ball restart for ``team`` at (x, y) in its own frame."""

    kind: SetPiece
    team: str
    x: float
    y: float
    related: Event | None = None
    penalty: bool = False


_Situation = _Carrier | _Loose | _Restart

_RESTART_PATTERN = {
    SetPiece.KICK_OFF: PlayPattern.FROM_KICKOFF,
    SetPiece.THROW_IN: PlayPattern.FROM_THROW_IN,
    SetPiece.CORNER: PlayPattern.FROM_CORNER,
    SetPiece.GOAL_KICK: PlayPattern.FROM_GOAL_KICK,
    SetPiece.FREE_KICK: PlayPattern.FROM_FREE_KICK,
}

_ROLE_SUB_WEIGHT = {
    Position.WG: 3.0,
    Position.ST: 3.0,
    Position.AM: 2.5,
    Position.CM: 2.0,
    Position.DM: 1.5,
    Position.FB: 1.0,
    Position.CB: 0.5,
}


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def _on_pitch(x: float, y: float) -> Point:
    return (
        round(_clamp(x, 0.0, PITCH_LENGTH_M), 1),
        round(_clamp(y, 0.0, PITCH_WIDTH_M), 1),
    )


def _ms(seconds: float) -> int:
    """Seconds to milliseconds, rounded to the 100 ms grid."""
    return max(100, round(seconds * 10) * 100)


@dataclass
class _Team:
    club: Club
    style: StyleParams
    slots: list[str | None]
    bench: list[str]
    sub_windows: list[tuple[int, int]]  # (period-2 clock_ms, number of subs)
    subs_made: int = 0
    used: set[str] = field(default_factory=set)


class _Simulator:
    def __init__(self, spec: MatchSpec, modifiers: ModifierProvider) -> None:
        league = load_league()
        self.rng = random.Random(spec.seed)
        self.spec = spec
        self.modifiers = modifiers
        self.home, self.away = spec.home_team_id, spec.away_team_id
        if self.home == self.away:
            raise ValueError("home and away teams must differ")
        home_club, away_club = league.club(self.home), league.club(self.away)
        self.match = Match(
            match_id=spec.match_id,
            competition=league.competition,
            season=league.season,
            kickoff_utc=spec.kickoff_utc,
            venue=home_club.venue,
            home_team_id=self.home,
            away_team_id=self.away,
            referee=self.rng.choice(league.referees),
            scenario_id=spec.scenario_id,
            generator_version=GENERATOR_VERSION,
            random_seed=spec.seed,
        )
        self.team_rows = [home_club.team(), away_club.team()]
        self.players = [*home_club.matchday_squad(), *away_club.matchday_squad()]
        self.by_id = {p.player_id: p for p in self.players}
        self.teams = {c.team_id: self._team_state(c) for c in (home_club, away_club)}

        self.events: list[Event] = []
        self.period = 1
        self.clock = 0
        self.period_offset_ms = 0
        self.poss_id = 0
        self.poss_team = self.home
        self.pattern = PlayPattern.FROM_KICKOFF
        self.score = {self.home: 0, self.away: 0}
        self.yellow: set[str] = set()
        self.lost_ball_at: dict[str, tuple[int, int]] = {}

    def _team_state(self, club: Club) -> _Team:
        squad = club.matchday_squad()
        starters = [p.player_id for p in squad if p.is_starter]
        bench = [p.player_id for p in squad if not p.is_starter]
        n_subs = self.rng.choices([3, 4, 5], weights=[3, 4, 4])[0]
        n_windows = min(n_subs, self.rng.choice([2, 3]))
        times = sorted(self.rng.randint(8, 42) * 60_000 for _ in range(n_windows))
        if self.rng.random() < 0.2:
            times[0] = 0  # a half-time change
        counts = [1] * n_windows
        for _ in range(n_subs - n_windows):
            counts[self.rng.randrange(n_windows)] += 1
        return _Team(
            club=club,
            style=STYLES[club.style_profile],
            slots=list(starters),
            bench=bench,
            sub_windows=list(zip(times, counts, strict=True)),
            used=set(starters),
        )

    # Helpers ---------------------------------------------------------------
    def other(self, team: str) -> str:
        return self.away if team == self.home else self.home

    def mods(self, team: str) -> Modifiers:
        return self.modifiers(team, self.period, self.clock)

    def role(self, player_id: str) -> Position:
        return self.by_id[player_id].position

    def advance(self, seconds: float) -> None:
        self.clock += _ms(seconds)

    def outfield(self, team: str) -> list[tuple[int, str]]:
        return [(i, p) for i, p in enumerate(self.teams[team].slots) if p is not None and i > 0]

    def keeper(self, team: str) -> str | None:
        return self.teams[team].slots[0]

    def shape_pos(self, team: str, slot: int, ball: Point, in_possession: bool) -> Point:
        club = self.teams[team].club
        return slot_position(
            club.formation, slot, ball[0], ball[1], in_possession, club.style_profile
        )

    def pick_near(self, team: str, at: Point, in_possession: bool, exclude: str = "") -> str:
        """An outfield player of ``team`` near ``at`` (team's frame), weighted by proximity."""
        options, weights = [], []
        for slot, pid in self.outfield(team):
            if pid == exclude:
                continue
            pos = self.shape_pos(team, slot, at, in_possession)
            options.append(pid)
            weights.append(1.0 / (1.0 + distance(*pos, *at) / 6.0) ** 3)
        return self.rng.choices(options, weights)[0]

    def pick_presser(self, team: str, at: Point) -> str:
        """A defender to engage the ball at ``at`` (defender's frame), favouring pressers."""
        options, weights = [], []
        for slot, pid in self.outfield(team):
            pos = self.shape_pos(team, slot, at, False)
            rating = self.by_id[pid].ratings.pressing / 70.0
            options.append(pid)
            weights.append(rating / (1.0 + distance(*pos, *at) / 6.0) ** 3)
        return self.rng.choices(options, weights)[0]

    def foot(self, player_id: str) -> BodyPart:
        foot = self.by_id[player_id].preferred_foot
        if foot is PreferredFoot.LEFT:
            return BodyPart.LEFT_FOOT
        if foot is PreferredFoot.BOTH:
            return self.rng.choice([BodyPart.RIGHT_FOOT, BodyPart.LEFT_FOOT])
        return BodyPart.RIGHT_FOOT

    def press_prob(self, defending: str, attack_x: float) -> float:
        """Chance the carrier at ``attack_x`` (attacker's frame) is pressed by ``defending``."""
        style = self.teams[defending].club.style_profile.value
        zone = attack_x / PITCH_LENGTH_M
        factor = {
            "high_press": 1.25 - 0.35 * zone,
            "low_block": 0.45 + 0.9 * zone,
            "counter": 0.6 + 0.6 * zone,
        }.get(style, 0.8 + 0.4 * zone)
        base = self.teams[defending].style.press * factor * self.mods(defending).press_mult
        return _clamp(base, 0.0, 0.85)

    def nearest_opponent(self, pressed: bool) -> float:
        if pressed:
            return round(self.rng.uniform(0.6, 4.9), 1)
        return round(self.rng.uniform(5.0, 14.0), 1)

    def set_possession(self, team: str, pattern: PlayPattern) -> None:
        if self.poss_id == 0 or team != self.poss_team:
            if self.poss_id > 0:
                self.lost_ball_at[self.poss_team] = (self.period, self.clock)
            self.poss_id += 1
            self.poss_team = team
        self.pattern = pattern

    def regain_pattern(self, team: str, x: float) -> tuple[PlayPattern, bool]:
        """Play pattern after an open-play regain at ``x``; counters start in the own half."""
        if x < 55 and self.rng.random() < self.teams[team].style.counter_rate:
            return PlayPattern.FROM_COUNTER, True
        return PlayPattern.REGULAR_PLAY, False

    def counterpress(self, team: str) -> bool:
        lost = self.lost_ball_at.get(team)
        return lost is not None and lost[0] == self.period and self.clock - lost[1] <= 5000

    def emit(
        self,
        event_type: EventType,
        team: str | None = None,
        player: str | None = None,
        outcome: Outcome | None = None,
        start: Point | None = None,
        end: Point | None = None,
        *,
        duration_ms: int | None = None,
        body_part: BodyPart | None = None,
        pressed: bool = False,
        nearest: float | None = None,
        receiver: str | None = None,
        related: tuple[Event | None, ...] = (),
        details: EventDetails | None = None,
    ) -> Event:
        seq = len(self.events) + 1
        start = _on_pitch(*start) if start is not None else None
        if start is not None and end is None:
            end = start
        end = _on_pitch(*end) if end is not None else None
        if player is not None and nearest is None:
            nearest = self.nearest_opponent(pressed)
        offset = timedelta(milliseconds=self.period_offset_ms + self.clock)
        event = Event(
            event_id=event_id(self.spec.match_id, seq),
            match_id=self.spec.match_id,
            seq_no=seq,
            period=self.period,
            clock_ms=self.clock,
            feed_ts_utc=self.spec.kickoff_utc + offset,
            team_id=team,
            player_id=player,
            event_type=event_type,
            outcome=outcome,
            start_x=start[0] if start else None,
            start_y=start[1] if start else None,
            end_x=end[0] if end else None,
            end_y=end[1] if end else None,
            body_part=body_part,
            duration_ms=duration_ms,
            possession_id=self.poss_id,
            possession_team_id=self.poss_team,
            play_pattern=self.pattern,
            under_pressure=pressed,
            nearest_opponent_m=nearest,
            receiver_player_id=receiver,
            related_event_ids=[e.event_id for e in related if e is not None],
            score_home=self.score[self.home],
            score_away=self.score[self.away],
            players_on_pitch_home=sum(p is not None for p in self.teams[self.home].slots),
            players_on_pitch_away=sum(p is not None for p in self.teams[self.away].slots),
            details=details if details is not None else NoDetails(),
        )
        self.events.append(event)
        return event

    # Match flow ------------------------------------------------------------
    def run(self) -> SimulatedMatch:
        first = self.rng.choice([self.home, self.away])
        ends = (
            PERIOD_MS + _ms(self.rng.uniform(60, 240)),
            PERIOD_MS + _ms(self.rng.uniform(180, 420)),
        )
        for period, end_ms, kicker in ((1, ends[0], first), (2, ends[1], self.other(first))):
            self.period, self.clock = period, 0
            self.set_possession(kicker, PlayPattern.FROM_KICKOFF)
            self.emit(EventType.PERIOD_START)
            situation: _Situation = _Restart(SetPiece.KICK_OFF, kicker, *CENTRE_SPOT)
            while self.clock < end_ms:
                situation = self.step(situation)
            self.emit(EventType.PERIOD_END)
            self.period_offset_ms = self.clock + HALF_TIME_BREAK_MS
        return SimulatedMatch(self.match, self.team_rows, self.players, self.events)

    def step(self, situation: _Situation) -> _Situation:
        if isinstance(situation, _Carrier):
            return self.carrier_step(situation)
        if isinstance(situation, _Loose):
            return self.loose_step(situation)
        return self.restart_step(situation)

    # Substitutions ---------------------------------------------------------
    def due_substitutions(self) -> None:
        if self.period != 2:
            return
        for team in (self.home, self.away):
            state = self.teams[team]
            while state.sub_windows and state.sub_windows[0][0] <= self.clock:
                _, count = state.sub_windows.pop(0)
                for _ in range(count):
                    if state.subs_made < MAX_SUBS:
                        self.substitute(team)

    def substitute(self, team: str) -> None:
        state = self.teams[team]
        on = self.outfield(team)
        if not on:
            return
        weights = [_ROLE_SUB_WEIGHT[self.role(pid)] for _, pid in on]
        slot, out_id = self.rng.choices(on, weights)[0]
        bench = [p for p in state.bench if p not in state.used and self.role(p) is not Position.GK]
        if not bench:
            return
        same_role = [p for p in bench if self.role(p) is self.role(out_id)]
        in_id = (same_role or bench)[0]
        self.emit(
            EventType.SUBSTITUTION,
            team,
            out_id,
            Outcome.SUCCESS,
            details=SubstitutionDetails(player_in_id=in_id, player_out_id=out_id),
        )
        state.slots[slot] = in_id
        state.used.add(in_id)
        state.subs_made += 1
        self.advance(self.rng.uniform(15, 35))

    # On the ball -----------------------------------------------------------
    def carrier_step(self, c: _Carrier) -> _Situation:
        team, opp = c.team, self.other(c.team)
        style = self.teams[team].style
        is_keeper = c.player == self.keeper(team)
        hold = self.rng.gauss(style.hold_s * (0.7 if c.counter else 1.0), 0.35)
        self.advance(max(0.3, hold * (0.6 if c.pressed else 1.0)))

        pressure_index: int | None = None
        last = c.last
        if c.pressed and not is_keeper:
            presser = self.pick_presser(opp, flip(c.x, c.y))
            pressure = self.emit(
                EventType.PRESSURE,
                opp,
                presser,
                Outcome.NO_EFFECT,
                flip(c.x, c.y),
                duration_ms=_ms(self.rng.uniform(0.6, 1.2)),
                nearest=round(self.rng.uniform(0.5, 2.5), 1),
                related=(c.last,),
                details=PressureDetails(
                    target_player_id=c.player, counterpress=self.counterpress(opp)
                ),
            )
            pressure_index = len(self.events) - 1
            self.clock += pressure.duration_ms or 0
            last = pressure
            roll = self.rng.random()
            turnover = self.mods(team).turnover_mult
            foul_p = 0.012 if in_attacking_box(c.x, c.y) else 0.04
            if roll < foul_p:
                return self.foul(opp, presser, c, pressure)
            if roll < foul_p + 0.056 * turnover:
                result = self.tackle(opp, presser, c, pressure, won_p=0.62)
                if isinstance(result, _Carrier) and result.team == team:
                    c, last = result, result.last
                else:
                    self.mark_forced_turnover(pressure_index)
                    return result

        result = self.act(c, last, is_keeper)
        if pressure_index is not None and self.lost_ball(result, team):
            self.mark_forced_turnover(pressure_index)
        return result

    def lost_ball(self, result: _Situation, team: str) -> bool:
        if isinstance(result, _Loose):
            return False
        return result.team != team

    def mark_forced_turnover(self, index: int) -> None:
        self.events[index] = self.events[index].model_copy(
            update={"outcome": Outcome.FORCED_TURNOVER}
        )

    def act(self, c: _Carrier, last: Event, is_keeper: bool) -> _Situation:
        """Choose and play the carrier's action: shot, dribble, carry or pass."""
        if is_keeper:
            return self.keeper_distribution(c, last)
        team = c.team
        style = self.teams[team].style
        ratings = self.by_id[c.player].ratings
        role = self.role(c.player)

        # A headed chance from a high cross or corner.
        if (
            c.assist is not None
            and isinstance(c.assist.details, PassDetails)
            and c.assist.details.height is PassHeight.HIGH
            and in_attacking_box(c.x, c.y)
            and self.rng.random() < 0.35
        ):
            return self.shot(c, ShotType.HEADER, last)

        p_shot = 0.0
        if c.x > 70 and abs(c.y - 34) < 24:
            xg = expected_goals(c.x, c.y)
            finishing = 0.75 + ratings.finishing / 200.0
            p_shot = style.shot_appetite * finishing * (0.052 + 0.7 * xg)
            p_shot *= self.mods(team).shot_rate_mult
            p_shot = min(p_shot, 0.45)
        attacker = role in (Position.WG, Position.AM, Position.ST)
        p_dribble = style.dribble_rate * (1.7 if attacker else 0.5) * (1.3 if c.x > 50 else 0.5)
        p_dribble *= ratings.pace / 75.0
        p_carry = style.carry_rate * (0.45 if c.pressed else 1.0) * (1.5 if c.counter else 1.0)
        p_carry *= 0.4 if c.carries >= 2 else 1.0

        roll = self.rng.random()
        if roll < p_shot:
            return self.shot(c, ShotType.OPEN_PLAY, last)
        if roll < p_shot + p_dribble:
            return self.dribble(c, last)
        if roll < p_shot + p_dribble + p_carry:
            return self.carry(c, last)
        return self.open_play_pass(c, last)

    def tackle(
        self, team: str, tackler: str, c: _Carrier, related: Event, won_p: float
    ) -> _Situation:
        """``team`` challenges carrier ``c``; a won tackle starts a new possession."""
        at = flip(c.x, c.y)
        won = self.rng.random() < won_p
        if won:
            pattern, counter = self.regain_pattern(team, at[0])
            self.set_possession(team, pattern)
        tackle = self.emit(
            EventType.TACKLE,
            team,
            tackler,
            Outcome.WON if won else Outcome.LOST,
            at,
            nearest=round(self.rng.uniform(0.3, 1.5), 1),
            related=(related,),
            details=DuelDetails(opponent_player_id=c.player),
        )
        self.advance(self.rng.uniform(0.3, 0.8))
        if won:
            pressed = self.rng.random() < self.press_prob(c.team, at[0]) * 0.6
            return _Carrier(team, tackler, *at, pressed, tackle, counter=counter)
        return _Carrier(c.team, c.player, c.x, c.y, False, tackle, c.assist, c.counter, c.carries)

    def foul(self, team: str, fouler: str, c: _Carrier, related: Event) -> _Situation:
        """``team`` fouls carrier ``c``: free kick (or penalty) and maybe a card."""
        penalty = in_attacking_box(c.x, c.y)
        foul = self.emit(
            EventType.FOUL,
            team,
            fouler,
            Outcome.PENALTY if penalty else Outcome.FREE_KICK,
            flip(c.x, c.y),
            related=(related,),
            details=FoulDetails(fouled_player_id=c.player),
            body_part=self.foot(fouler),
        )
        self.advance(self.rng.uniform(12, 28))
        if self.rng.random() < (0.45 if penalty else 0.16):
            second = fouler in self.yellow
            self.emit(
                EventType.CARD,
                team,
                fouler,
                Outcome.SECOND_YELLOW if second else Outcome.YELLOW,
                related=(foul,),
                details=CardDetails(reason=CardReason.SECOND_YELLOW if second else CardReason.FOUL),
            )
            self.yellow.add(fouler)
            if second:
                slots = self.teams[team].slots
                slots[slots.index(fouler)] = None
            self.advance(self.rng.uniform(15, 30))
        if penalty:
            return _Restart(SetPiece.FREE_KICK, c.team, PENALTY_SPOT_X, 34.0, foul, penalty=True)
        return _Restart(SetPiece.FREE_KICK, c.team, c.x, c.y, foul)

    def dribble(self, c: _Carrier, last: Event) -> _Situation:
        opp = self.other(c.team)
        defender = self.pick_presser(opp, flip(c.x, c.y))
        pace = self.by_id[c.player].ratings.pace
        success = self.rng.random() < _clamp(0.45 + (pace - 70) / 120.0, 0.25, 0.75)
        end = _on_pitch(c.x + self.rng.uniform(2, 5), c.y + self.rng.gauss(0, 2))
        dribble = self.emit(
            EventType.DRIBBLE,
            c.team,
            c.player,
            Outcome.SUCCESS if success else Outcome.FAIL,
            (c.x, c.y),
            end,
            duration_ms=_ms(self.rng.uniform(0.6, 1.4)),
            pressed=True,
            related=(last,),
            body_part=self.foot(c.player),
        )
        self.clock += dribble.duration_ms or 0
        if success:
            if self.rng.random() < 0.6:
                self.emit(
                    EventType.TACKLE,
                    opp,
                    defender,
                    Outcome.LOST,
                    flip(*end),
                    nearest=round(self.rng.uniform(0.3, 1.5), 1),
                    related=(dribble,),
                    details=DuelDetails(opponent_player_id=c.player),
                )
            pressed = self.rng.random() < 0.25
            return _Carrier(c.team, c.player, *end, pressed, dribble, None, c.counter, c.carries)
        if self.rng.random() < 0.12:
            return self.foul(
                opp, defender, _Carrier(c.team, c.player, *end, True, dribble), dribble
            )
        return self.tackle(
            opp, defender, _Carrier(c.team, c.player, *end, True, dribble), dribble, won_p=1.0
        )

    def carry(self, c: _Carrier, last: Event) -> _Situation:
        length = self.rng.uniform(8, 22) if c.counter else self.rng.uniform(3, 14)
        angle = self.rng.gauss(0, 0.6)
        if c.x > 80:  # work across the edge of the box rather than straight at goal
            angle = math.atan2(34 - c.y, 100 - c.x) + self.rng.choice([-1, 1]) * 0.9
            length *= 0.6
        end = _on_pitch(c.x + length * math.cos(angle), c.y + length * math.sin(angle))
        max_x = 96.0 if c.counter else 92.0
        end = (_clamp(end[0], 1.0, max(max_x, c.x)), _clamp(end[1], 1.0, 67.0))
        dispossessed = (
            self.rng.random() < (0.07 if c.pressed else 0.025) * self.mods(c.team).turnover_mult
        )
        carry = self.emit(
            EventType.CARRY,
            c.team,
            c.player,
            Outcome.DISPOSSESSED if dispossessed else Outcome.SUCCESS,
            (c.x, c.y),
            end,
            duration_ms=_ms(distance(c.x, c.y, *end) / self.rng.uniform(4.0, 6.5)),
            pressed=c.pressed,
            related=(last,),
        )
        self.clock += carry.duration_ms or 0
        if dispossessed:
            return _Loose(c.team, *end, keep=0.2, related=carry)
        pressed = self.rng.random() < self.press_prob(self.other(c.team), end[0])
        return _Carrier(c.team, c.player, *end, pressed, carry, None, c.counter, c.carries + 1)

    # Passing ---------------------------------------------------------------
    def open_play_pass(self, c: _Carrier, last: Event) -> _Situation:
        team = c.team
        style = self.teams[team].style
        rng = self.rng
        candidates = [(s, p) for s, p in self.outfield(team) if p != c.player]
        if not candidates:
            return self.carry(c, last)

        # Cross from a wide attacking position.
        if c.x > 78 and (c.y < 18 or c.y > 50) and rng.random() < 0.4:
            target = (rng.uniform(88, 99), rng.uniform(26, 42))
            receiver = self.pick_near(team, target, True, exclude=c.player)
            height = PassHeight.HIGH if rng.random() < 0.7 else PassHeight.LOW
            return self.make_pass(c, receiver, target, PassType.CROSS, height, SetPiece.NONE, last)

        # Long ball toward the front players.
        if c.x < 60 and rng.random() < style.long_rate * (1.6 if c.pressed else 1.0):
            front = [
                (s, p)
                for s, p in candidates
                if self.role(p) in (Position.ST, Position.WG, Position.AM)
            ] or candidates
            slot, receiver = rng.choice(front)
            pos = self.shape_pos(team, slot, (c.x, c.y), True)
            target = (pos[0] + rng.uniform(0, 8), pos[1] + rng.gauss(0, 4))
            return self.make_pass(c, receiver, target, None, None, SetPiece.NONE, last)

        weights = []
        positions = []
        for slot, _pid in candidates:
            pos = self.shape_pos(team, slot, (c.x, c.y), True)
            dx = pos[0] - c.x
            d = distance(c.x, c.y, *pos)
            w = math.exp(-d / 15.0) * (1 + style.forward_bias * max(0.0, dx) / 10.0)
            if dx < -12:
                w *= 0.5
            if c.pressed and dx < 0:
                w *= 1.8
            positions.append(pos)
            weights.append(w)
        index = rng.choices(range(len(candidates)), weights)[0]
        slot, receiver = candidates[index]
        pos = positions[index]
        target = (pos[0] + rng.gauss(0, 2.5), pos[1] + rng.gauss(0, 2.5))
        forced: PassType | None = None
        if (
            self.role(receiver) in (Position.ST, Position.WG, Position.AM)
            and pos[0] > c.x + 6
            and pos[0] > 55
            and rng.random() < 0.2
        ):
            forced = PassType.THROUGH_BALL
            target = (min(pos[0] + rng.uniform(3, 8), 96.0), pos[1] + rng.gauss(0, 3))
        return self.make_pass(c, receiver, target, forced, None, SetPiece.NONE, last)

    def classify_pass(self, start: Point, end: Point) -> PassType:
        length = distance(*start, *end)
        if end[0] < start[0] - 4:
            return PassType.BACK_PASS
        if abs(end[1] - start[1]) > 28 and length > 28:
            return PassType.SWITCH
        if length > 30:
            return PassType.LONG
        return PassType.SHORT

    def pass_height(self, pass_type: PassType) -> PassHeight:
        roll = self.rng.random()
        if pass_type in (PassType.LONG, PassType.SWITCH):
            return PassHeight.HIGH if roll < 0.6 else PassHeight.LOW
        if pass_type is PassType.CROSS:
            return PassHeight.HIGH if roll < 0.75 else PassHeight.LOW
        return PassHeight.GROUND if roll < 0.9 else PassHeight.LOW

    def make_pass(
        self,
        c: _Carrier,
        receiver: str,
        target: Point,
        pass_type: PassType | None,
        height: PassHeight | None,
        set_piece: SetPiece,
        last: Event | None,
        body_part: BodyPart | None = None,
    ) -> _Situation:
        """Play a pass from ``c`` to ``receiver`` aiming at ``target``; resolve the outcome."""
        rng = self.rng
        team, opp = c.team, self.other(c.team)
        start = _on_pitch(c.x, c.y)
        end = _on_pitch(_clamp(target[0], 0.5, 104.5), _clamp(target[1], 0.5, 67.5))
        pass_type = pass_type or self.classify_pass(start, end)
        height = height or self.pass_height(pass_type)
        length = distance(*start, *end)
        fwd = forward_progress(start[0], end[0])
        p = pass_completion_probability(
            length,
            c.pressed,
            fwd,
            height is PassHeight.HIGH,
            pass_type in (PassType.THROUGH_BALL, PassType.CROSS),
        )
        p = _clamp(p + self.mods(team).pass_success_add, 0.01, 0.99)
        speed_kmh = rng.uniform(55, 85) if height is PassHeight.HIGH else rng.uniform(35, 65)
        flight = _ms(max(length, 1.0) / (speed_kmh / 3.6))
        details = PassDetails(pass_type=pass_type, height=height, set_piece=set_piece)
        body = body_part or self.foot(c.player)
        related = (last,)

        if rng.random() < p:
            sent = self.emit(
                EventType.PASS,
                team,
                c.player,
                Outcome.COMPLETE,
                start,
                end,
                duration_ms=flight,
                body_part=body,
                pressed=c.pressed,
                receiver=receiver,
                related=related,
                details=details,
            )
            self.clock += flight
            pressed = rng.random() < self.press_prob(opp, end[0])
            miscontrol = (0.02 + 0.04 * pressed + 0.04 * (height is PassHeight.HIGH)) * self.mods(
                team
            ).turnover_mult
            if rng.random() < miscontrol:
                bad = self.emit(
                    EventType.BALL_RECEIPT,
                    team,
                    receiver,
                    Outcome.MISCONTROL,
                    end,
                    pressed=pressed,
                    related=(sent,),
                )
                self.advance(rng.uniform(0.4, 1.0))
                return _Loose(team, end[0] + 1.5, end[1], keep=0.3, related=bad)
            receipt = self.emit(
                EventType.BALL_RECEIPT,
                team,
                receiver,
                Outcome.SUCCESS,
                end,
                pressed=pressed,
                related=(sent,),
            )
            return _Carrier(team, receiver, *end, pressed, receipt, sent, c.counter)

        # The pass fails: offside, out of play, or cut out. A pass row always stores the
        # intended target as its end, so difficulty recomputed from the row equals the
        # probability that decided the outcome; the next event shows where the ball went.
        roll = rng.random()
        offside_p = 0.0
        if pass_type in (PassType.THROUGH_BALL, PassType.LONG) and end[0] > 70:
            offside_p = 0.18
        out_p = {
            PassType.LONG: 0.22,
            PassType.SWITCH: 0.25,
            PassType.CROSS: 0.3,
            PassType.THROUGH_BALL: 0.15,
        }.get(pass_type, 0.08)
        if set_piece is SetPiece.KICK_OFF:
            offside_p = out_p = 0.0

        if roll < offside_p:
            sent = self.emit(
                EventType.PASS,
                team,
                c.player,
                Outcome.OFFSIDE,
                start,
                end,
                duration_ms=flight,
                body_part=body,
                pressed=c.pressed,
                receiver=receiver,
                related=related,
                details=details,
            )
            self.clock += flight
            self.advance(rng.uniform(10, 20))
            return _Restart(SetPiece.FREE_KICK, opp, *flip(*end), sent)

        if roll < offside_p + out_p:
            over_byline = pass_type is PassType.CROSS or end[0] > 95
            if over_byline:
                out_at: Point = (PITCH_LENGTH_M, end[1])
            else:
                out_at = (end[0], 0.0 if end[1] < PITCH_WIDTH_M / 2 else PITCH_WIDTH_M)
            sent = self.emit(
                EventType.PASS,
                team,
                c.player,
                Outcome.OUT_OF_PLAY,
                start,
                end,
                duration_ms=flight,
                body_part=body,
                pressed=c.pressed,
                receiver=receiver,
                related=related,
                details=details,
            )
            self.clock += sent.duration_ms or 0
            return self.out_of_play(sent, team, out_at, over_byline)

        frac = rng.uniform(0.45, 1.0)
        cut = _on_pitch(
            start[0] + frac * (end[0] - start[0]), start[1] + frac * (end[1] - start[1])
        )
        self.emit(
            EventType.PASS,
            team,
            c.player,
            Outcome.INCOMPLETE,
            start,
            end,
            duration_ms=flight,
            body_part=body,
            pressed=c.pressed,
            receiver=receiver,
            related=related,
            details=details,
        )
        sent = self.events[-1]
        self.clock += _ms(max(frac * length, 1.0) / (speed_kmh / 3.6))
        if rng.random() < 0.5:
            at = flip(*cut)
            defender = self.pick_presser(opp, at)
            pattern, counter = self.regain_pattern(opp, at[0])
            self.set_possession(opp, pattern)
            won = self.emit(
                EventType.INTERCEPTION,
                opp,
                defender,
                Outcome.WON,
                at,
                related=(sent,),
                details=DuelDetails(opponent_player_id=receiver),
            )
            pressed = rng.random() < self.press_prob(team, at[0]) * 0.6
            return _Carrier(opp, defender, *at, pressed, won, counter=counter)
        return _Loose(team, *cut, keep=0.2, related=sent)

    def out_of_play(self, cause: Event, last_team: str, at: Point, over_byline: bool) -> _Situation:
        """The ball leaves the pitch at ``at`` (``last_team``'s frame) off ``last_team``."""
        opp = self.other(last_team)
        kind = Outcome.GOAL_KICK if over_byline else Outcome.THROW_IN
        self.advance(self.rng.uniform(0.3, 1.0))
        self.emit(EventType.OUT_OF_PLAY, outcome=kind, related=(cause,))
        if over_byline:
            self.advance(self.rng.uniform(22, 40))
            return _Restart(SetPiece.GOAL_KICK, opp, 5.5, self.rng.choice([25.0, 43.0]), cause)
        self.advance(self.rng.uniform(10, 24))
        x, y = flip(*at)
        return _Restart(SetPiece.THROW_IN, opp, _clamp(x, 1.0, 104.0), y, cause)

    # Shots -----------------------------------------------------------------
    def shot(self, c: _Carrier, shot_type: ShotType, last: Event | None) -> _Situation:
        rng = self.rng
        team, opp = c.team, self.other(c.team)
        assist = c.assist if isinstance(c.assist, Event) else None
        assist_type = (
            assist.details.pass_type if assist and isinstance(assist.details, PassDetails) else None
        )
        through = assist_type is PassType.THROUGH_BALL
        header = shot_type is ShotType.HEADER
        xg = expected_goals(c.x, c.y, header, through, penalty=shot_type is ShotType.PENALTY)
        speed = {
            ShotType.HEADER: rng.uniform(50, 78),
            ShotType.PENALTY: rng.uniform(80, 112),
            ShotType.FREE_KICK: rng.uniform(75, 118),
        }.get(shot_type, _clamp(rng.gauss(88, 14), 55, 126))

        if rng.random() < xg:
            outcome = Outcome.GOAL
        else:
            far = distance(c.x, c.y, PITCH_LENGTH_M, 34.0) > 16
            blocked = 0.0 if shot_type is ShotType.PENALTY else (0.34 if far else 0.2)
            outcome = rng.choices(
                [Outcome.BLOCKED, Outcome.POST, Outcome.SAVED, Outcome.OFF_TARGET],
                [blocked, 0.025, 0.3, 0.42],
            )[0]
        if outcome is Outcome.GOAL:
            end: Point = (PITCH_LENGTH_M, rng.uniform(30.8, 37.2))
        elif outcome is Outcome.SAVED:
            end = (rng.uniform(103.0, 105.0), rng.uniform(31.0, 37.0))
        elif outcome is Outcome.POST:
            end = (PITCH_LENGTH_M, rng.choice([POST_LOW_Y, POST_HIGH_Y]))
        elif outcome is Outcome.BLOCKED:
            step = rng.uniform(1.5, 4.0)
            angle = math.atan2(34 - c.y, PITCH_LENGTH_M - c.x)
            end = (c.x + step * math.cos(angle), c.y + step * math.sin(angle))
        else:
            wide = rng.random() < 0.7
            end = (
                PITCH_LENGTH_M,
                rng.choice([rng.uniform(22, 30.2), rng.uniform(37.8, 46)])
                if wide
                else rng.uniform(31, 37),
            )
        travel = distance(c.x, c.y, *end)
        keeper = self.keeper(opp)
        body = BodyPart.HEAD if header else self.foot(c.player)
        shot = self.emit(
            EventType.SHOT,
            team,
            c.player,
            outcome,
            (c.x, c.y),
            end,
            duration_ms=_ms(max(travel, 1.0) / (speed / 3.6)),
            body_part=body,
            pressed=c.pressed,
            related=(assist or last,),
            details=ShotDetails(
                shot_type=shot_type,
                shot_speed_kmh=round(speed, 1),
                on_target=outcome in (Outcome.GOAL, Outcome.SAVED),
                goalkeeper_id=keeper,
                assist_event_id=assist.event_id if assist else None,
                assist_type=assist_type,
            ),
        )
        self.clock += shot.duration_ms or 0

        if outcome is Outcome.GOAL:
            self.score[team] += 1
            self.advance(rng.uniform(50, 90))
            return _Restart(SetPiece.KICK_OFF, opp, *CENTRE_SPOT, shot)
        if outcome is Outcome.OFF_TARGET:
            return self.out_of_play(shot, team, end, over_byline=True)
        if outcome is Outcome.SAVED and keeper is not None:
            gk_at = flip(*end)
            if rng.random() < 0.45:
                self.set_possession(opp, PlayPattern.REGULAR_PLAY)
                caught = self.emit(
                    EventType.GOALKEEPER,
                    opp,
                    keeper,
                    Outcome.CATCH,
                    gk_at,
                    related=(shot,),
                    body_part=BodyPart.OTHER,
                    details=GoalkeeperDetails(action=GoalkeeperAction.CATCH),
                )
                self.advance(rng.uniform(4, 10))
                return _Carrier(opp, keeper, 6.0, 34.0, False, caught)
            action = rng.choice([GoalkeeperAction.DIVE_SAVE, GoalkeeperAction.STANDING_SAVE])
            saved = self.emit(
                EventType.GOALKEEPER,
                opp,
                keeper,
                Outcome.SAVE,
                gk_at,
                related=(shot,),
                body_part=BodyPart.OTHER,
                details=GoalkeeperDetails(action=action),
            )
            if rng.random() < 0.4:
                return self.corner_after(saved, team, end)
            return _Loose(
                opp, gk_at[0] + rng.uniform(3, 9), gk_at[1] + rng.gauss(0, 6), 0.65, saved
            )
        if outcome is Outcome.BLOCKED:
            at = flip(*end)
            blocker = self.pick_presser(opp, at)
            block = self.emit(
                EventType.BLOCK,
                opp,
                blocker,
                Outcome.SUCCESS,
                at,
                related=(shot,),
                body_part=BodyPart.OTHER,
            )
            if rng.random() < 0.25:
                return self.corner_after(block, team, end)
            return _Loose(opp, at[0] + rng.uniform(1, 6), at[1] + rng.gauss(0, 5), 0.55, block)
        # Post (or a save with no keeper): rebound or out for a goal kick.
        if rng.random() < 0.25:
            return self.out_of_play(shot, team, end, over_byline=True)
        rebound = (rng.uniform(94, 101), rng.uniform(27, 41))
        return _Loose(team, *rebound, keep=0.35, related=shot)

    def corner_after(self, cause: Event, attacking: str, near: Point) -> _Situation:
        self.advance(self.rng.uniform(0.3, 1.0))
        self.emit(EventType.OUT_OF_PLAY, outcome=Outcome.CORNER, related=(cause,))
        self.advance(self.rng.uniform(22, 40))
        side = 0.5 if near[1] < PITCH_WIDTH_M / 2 else PITCH_WIDTH_M - 0.5
        return _Restart(SetPiece.CORNER, attacking, PITCH_LENGTH_M - 0.5, side, cause)

    # Loose balls and restarts ----------------------------------------------
    def loose_step(self, loose: _Loose) -> _Situation:
        rng = self.rng
        keep = rng.random() < loose.keep
        winner = loose.team if keep else self.other(loose.team)
        at = _on_pitch(loose.x, loose.y) if keep else flip(*_on_pitch(loose.x, loose.y))
        at = (_clamp(at[0], 1.0, 104.0), _clamp(at[1], 1.0, 67.0))
        self.advance(rng.uniform(0.6, 1.8))
        player = self.pick_near(winner, at, keep)

        if not keep and at[0] < 22 and rng.random() < 0.5:
            # A defender in their own third clears rather than controls.
            end = (
                _clamp(at[0] + rng.uniform(30, 50), 1, 104),
                _clamp(at[1] + rng.gauss(0, 12), 1, 67),
            )
            clearance = self.emit(
                EventType.CLEARANCE,
                winner,
                player,
                Outcome.SUCCESS,
                at,
                end,
                duration_ms=_ms(distance(*at, *end) / 20.0),
                body_part=self.foot(player),
                related=(loose.related,),
            )
            self.clock += clearance.duration_ms or 0
            if rng.random() < 0.12:
                touch: Point = (end[0], 0.0 if end[1] < 34 else PITCH_WIDTH_M)
                return self.out_of_play(clearance, winner, touch, over_byline=False)
            return _Loose(winner, *end, keep=0.45, related=clearance)

        counter = False
        if winner != self.poss_team:
            pattern, counter = self.regain_pattern(winner, at[0])
            self.set_possession(winner, pattern)
        recovery = self.emit(
            EventType.BALL_RECOVERY,
            winner,
            player,
            Outcome.SUCCESS,
            at,
            related=(loose.related,),
        )
        pressed = rng.random() < self.press_prob(self.other(winner), at[0]) * 0.7
        return _Carrier(winner, player, *at, pressed, recovery, counter=counter)

    def restart_step(self, r: _Restart) -> _Situation:
        self.due_substitutions()
        rng = self.rng
        team = r.team
        self.set_possession(team, _RESTART_PATTERN[r.kind])
        last = r.related

        if r.kind is SetPiece.KICK_OFF:
            kicker = self.pick_near(team, CENTRE_SPOT, True)
            target = (CENTRE_SPOT[0] - rng.uniform(6, 14), 34 + rng.gauss(0, 7))
            receiver = self.pick_near(team, target, True, exclude=kicker)
            c = _Carrier(team, kicker, *CENTRE_SPOT, False, last or self.events[-1])
            return self.make_pass(
                c, receiver, target, PassType.BACK_PASS, PassHeight.GROUND, SetPiece.KICK_OFF, last
            )

        if r.kind is SetPiece.THROW_IN:
            thrower = self.pick_near(team, (r.x, r.y), True)
            inward = 1.0 if r.y < 34 else -1.0
            target = (r.x + rng.uniform(-4, 12), r.y + inward * rng.uniform(6, 14))
            receiver = self.pick_near(team, target, True, exclude=thrower)
            pressed = rng.random() < 0.2
            c = _Carrier(team, thrower, r.x, r.y, pressed, last or self.events[-1])
            return self.make_pass(
                c,
                receiver,
                target,
                None,
                PassHeight.LOW,
                SetPiece.THROW_IN,
                last,
                body_part=BodyPart.OTHER,
            )

        if r.kind is SetPiece.GOAL_KICK:
            keeper = self.keeper(team) or self.pick_near(team, (5.5, 34), True)
            c = _Carrier(team, keeper, r.x, r.y, False, last or self.events[-1])
            return self.keeper_distribution(c, last, set_piece=SetPiece.GOAL_KICK)

        if r.kind is SetPiece.CORNER:
            taker = self.pick_near(team, (95.0, r.y), True)
            c = _Carrier(team, taker, r.x, r.y, False, last or self.events[-1])
            if rng.random() < 0.25:
                target = (rng.uniform(92, 100), abs(r.y - rng.uniform(6, 14)))
                receiver = self.pick_near(team, target, True, exclude=taker)
                return self.make_pass(
                    c, receiver, target, PassType.SHORT, PassHeight.GROUND, SetPiece.CORNER, last
                )
            target = (rng.uniform(94, 101), rng.uniform(28, 40))
            receiver = self.pick_near(team, target, True, exclude=taker)
            return self.make_pass(
                c, receiver, target, PassType.CROSS, PassHeight.HIGH, SetPiece.CORNER, last
            )

        # Free kick or penalty.
        if r.penalty:
            shooter = max(
                (p for _, p in self.outfield(team)),
                key=lambda p: (self.by_id[p].ratings.finishing, p),
            )
            c = _Carrier(team, shooter, r.x, r.y, False, last or self.events[-1])
            return self.shot(c, ShotType.PENALTY, last)
        taker = self.pick_near(team, (r.x, r.y), True)
        c = _Carrier(team, taker, r.x, r.y, False, last or self.events[-1])
        if r.x > 72 and abs(r.y - 34) < 16 and rng.random() < 0.35:
            return self.shot(c, ShotType.FREE_KICK, last)
        if r.x > 70:
            target = (rng.uniform(93, 101), rng.uniform(27, 41))
            receiver = self.pick_near(team, target, True, exclude=taker)
            return self.make_pass(
                c, receiver, target, PassType.CROSS, PassHeight.HIGH, SetPiece.FREE_KICK, last
            )
        slot, receiver = rng.choice([(s, p) for s, p in self.outfield(team) if p != taker])
        pos = self.shape_pos(team, slot, (r.x, r.y), True)
        return self.make_pass(c, receiver, pos, None, None, SetPiece.FREE_KICK, last)

    def keeper_distribution(
        self, c: _Carrier, last: Event | None, set_piece: SetPiece = SetPiece.NONE
    ) -> _Situation:
        """Goalkeeper plays it short to a defender or long to the front players."""
        rng = self.rng
        team = c.team
        style = self.teams[team].style
        outfield = self.outfield(team)
        if rng.random() < style.short_goal_kick:
            options = [
                (s, p)
                for s, p in outfield
                if self.role(p) in (Position.CB, Position.FB, Position.DM)
            ] or outfield
            slot, receiver = rng.choice(options)
            pos = self.shape_pos(team, slot, (c.x + 10, c.y), True)
            target = (_clamp(pos[0] - 8, 8, 35), pos[1])
        else:
            options = [
                (s, p)
                for s, p in outfield
                if self.role(p) in (Position.ST, Position.WG, Position.AM)
            ] or outfield
            slot, receiver = rng.choice(options)
            pos = self.shape_pos(team, slot, (50.0, 34.0), True)
            target = (rng.uniform(50, 68), pos[1] + rng.gauss(0, 5))
        return self.make_pass(c, receiver, target, None, None, set_piece, last)


def simulate_match(spec: MatchSpec, modifiers: ModifierProvider = no_modifiers) -> SimulatedMatch:
    """Generate the reference rows and event stream for one match."""
    return _Simulator(spec, modifiers).run()
