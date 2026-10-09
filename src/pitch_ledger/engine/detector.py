"""Moment detector: turns the event (and tracking) stream into evidence-backed triggers.

Every rule reads only data up to the moment it fires, so replaying a match live gives
the same triggers as running over the finished match. Numbers in ``metrics`` are
rounded as they would be shown (whole numbers for counts and scores, one decimal for
percentages, speeds and distances, two for xG).

| Rule | Insight type | Fires when |
| --- | --- | --- |
| momentum_shift | momentum_shift | a team's build-up or pressing evidence crosses the threshold |
| goal | key_moment | a shot is scored |
| sending_off | key_moment | a red or second yellow card |
| penalty_awarded | key_moment | a foul gives a penalty |
| sending_off_impact | key_moment | 10 minutes after a sending-off |
| player_form | player_form | 5+ non-penalty shots worth 0.8+ xG by one player in 15 minutes |
| pass_milestone | milestone | a player completes his 50th, 75th or 100th pass |
| brace / hat_trick | milestone | a player's second or third goal |
| distance_milestone | milestone | a player passes 5 or 10 km (tracking) |
| shot_speed | speed_alert | a shot above 100 km/h (auto-approved) |
| top_speed | speed_alert | a player's first sample at 34+ km/h (tracking, auto-approved) |
| pass_quality | pass_quality | a completed pass of difficulty 65+ with 15+ m forward progress |
| half_time_recap / full_time_recap | recap | at each period end |
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pitch_ledger.engine.metrics import (
    is_counted_pass,
    pass_ball_speed_kmh,
    pass_difficulty_of,
    pass_forward_m,
    pass_length_m,
    shot_speed_kmh,
    shot_xg,
)
from pitch_ledger.engine.momentum import MomentumAlert, MomentumConfig, momentum_alerts
from pitch_ledger.engine.physical import HSR_LOW_KMH, physical
from pitch_ledger.engine.windows import MatchEngine, match_minute
from pitch_ledger.schema import (
    CardDetails,
    Event,
    InsightSubject,
    Match,
    ShotDetails,
    TrackingSample,
    Trigger,
)
from pitch_ledger.schema.enums import EventType, InsightType, Outcome, ShotType
from pitch_ledger.validation.lineup import SENDING_OFF

MIN = 60_000


@dataclass(frozen=True)
class DetectorConfig:
    momentum: MomentumConfig = field(default_factory=MomentumConfig)
    momentum_before_ms: int = 10 * MIN
    form_window_ms: int = 15 * MIN
    form_min_shots: int = 5
    form_min_xg: float = 0.8
    form_cooldown_ms: int = 20 * MIN
    pass_milestones: tuple[int, ...] = (50, 75, 100)
    distance_milestones_km: tuple[float, ...] = (5.0, 10.0)
    shot_speed_alert_kmh: float = 100.0
    top_speed_alert_kmh: float = 34.0
    pass_quality_min_difficulty: float = 65.0
    pass_quality_min_forward_m: float = 15.0
    pass_quality_cooldown_ms: int = 10 * MIN
    sending_off_impact_ms: int = 10 * MIN


@dataclass(frozen=True)
class _Draft:
    """A trigger before IDs are assigned; ``order`` breaks ties at the same clock."""

    period: int
    clock_ms: int
    order: int
    insight_type: InsightType
    rule_id: str
    subject: InsightSubject
    metrics: dict[str, int | float]
    evidence: tuple[str, ...]
    context: dict[str, Any] = field(default_factory=dict)
    auto_approve: bool = False


def _pct(value: float | None) -> float:
    return round(100.0 * value, 1) if value is not None else 0.0


def _minute(period: int, clock_ms: int) -> float:
    return round(match_minute(period, clock_ms), 1)


def _window(period: int, start_ms: int, end_ms: int) -> dict[str, int]:
    return {"period": period, "start_ms": start_ms, "end_ms": end_ms}


class Detector:
    """Runs every rule over one match."""

    def __init__(self, match: Match, config: DetectorConfig | None = None) -> None:
        self.match = match
        self.config = config or DetectorConfig()

    def detect(
        self, events: Sequence[Event], tracking: Sequence[TrackingSample] = ()
    ) -> list[Trigger]:
        engine = MatchEngine(self.match, events)
        drafts = [
            *self._momentum(engine, tracking),
            *self._key_moments(engine),
            *self._player_form(events),
            *self._event_milestones(events),
            *self._shot_speed(events),
            *self._pass_quality(events),
            *self._recaps(engine),
            *self._tracking_rules(tracking),
        ]
        drafts.sort(key=lambda d: (d.period, d.clock_ms, d.order))
        return [
            Trigger(
                trigger_id=f"{self.match.match_id}-T{i:04d}",
                match_id=self.match.match_id,
                period=d.period,
                clock_ms=d.clock_ms,
                insight_type=d.insight_type,
                rule_id=d.rule_id,
                subject=d.subject,
                metrics=d.metrics,
                evidence_event_ids=list(d.evidence),
                context=d.context,
                auto_approve=d.auto_approve,
            )
            for i, d in enumerate(drafts, start=1)
        ]

    # Momentum ---------------------------------------------------------------
    def _momentum(self, engine: MatchEngine, tracking: Sequence[TrackingSample]) -> list[_Draft]:
        cfg = self.config
        out = []
        for alert in momentum_alerts(engine.events, engine.teams, cfg.momentum):
            out.append(self._momentum_draft(engine, alert, tracking))
        return out

    def _momentum_draft(
        self, engine: MatchEngine, alert: MomentumAlert, tracking: Sequence[TrackingSample]
    ) -> _Draft:
        team, opp, p = alert.team_id, engine.other(alert.team_id), alert.period
        onset, now = alert.onset_ms, alert.clock_ms
        start = max(0, onset - self.config.momentum_before_ms)
        before = engine.team_window(team, p, start, onset)
        after = engine.team_window(team, p, onset, now)
        opp_before = engine.team_window(opp, p, start, onset)
        opp_after = engine.team_window(opp, p, onset, now)
        passes = [
            e for e in engine.events_in(p, onset, now) if is_counted_pass(e) and e.team_id == team
        ]
        expected = (
            sum(1 - pass_difficulty_of(e) / 100 for e in passes) / len(passes) if passes else None
        )
        pressured = sum(e.under_pressure for e in passes) / len(passes) if passes else None
        metrics: dict[str, int | float] = {
            "control_before": round(before.control),
            "control_after": round(after.control),
            "possession_before": _pct(before.possession_share),
            "possession_after": _pct(after.possession_share),
            "pass_accuracy_before": _pct(before.pass_accuracy),
            "pass_accuracy_after": _pct(after.pass_accuracy),
            "expected_pass_accuracy_after": _pct(expected),
            "pressured_passes_after": _pct(pressured),
            "passes_after": after.passes,
            "turnovers_after": after.turnovers,
            "own_half_turnovers_after": after.own_half_turnovers,
            "minutes_since_onset": round((now - onset) / MIN, 1),
            "evidence_score": round(alert.score, 1),
        }
        if opp_before.ppda is not None:
            metrics["opponent_ppda_before"] = round(opp_before.ppda, 1)
        if opp_after.ppda is not None:
            metrics["opponent_ppda_after"] = round(opp_after.ppda, 1)
        if tracking:
            span_min = max((now - onset) / MIN, 1e-9)
            hsr_before = self._team_hsr_per_min(tracking, team, p, start, onset)
            hsr_after = self._team_hsr_per_min(tracking, team, p, onset, now, span_min)
            metrics["team_hsr_m_per_min_before"] = hsr_before
            metrics["team_hsr_m_per_min_after"] = hsr_after
        evidence = sorted({e.event_id for e in passes} | set(after.turnover_event_ids))
        return _Draft(
            p,
            now,
            0,
            InsightType.MOMENTUM_SHIFT,
            "momentum_shift",
            InsightSubject(team_id=team),
            metrics,
            tuple(evidence),
            {
                "direction": "fall",
                "detector": alert.detector,
                "opponent_team_id": opp,
                "onset_minute": _minute(p, onset),
                "before_window": _window(p, start, onset),
                "after_window": _window(p, onset, now),
            },
        )

    def _team_hsr_per_min(
        self,
        tracking: Sequence[TrackingSample],
        team: str,
        period: int,
        start_ms: int,
        end_ms: int,
        minutes: float | None = None,
    ) -> float:
        """High-speed running (m per minute, summed over the team's players)."""
        stats = physical(
            (s for s in tracking if s.player_id.startswith(team)), period, start_ms, end_ms
        )
        span = minutes if minutes is not None else max((end_ms - start_ms) / MIN, 1e-9)
        return round(sum(p.hsr_m + p.sprint_m for p in stats.values()) / span, 1)

    # Key moments ------------------------------------------------------------
    def _key_moments(self, engine: MatchEngine) -> list[_Draft]:
        out = []
        by_id = {e.event_id: e for e in engine.events}
        goals: dict[str, int] = defaultdict(int)
        for e in engine.events:
            if e.event_type is EventType.SHOT and e.outcome is Outcome.GOAL and e.team_id:
                goals[e.player_id or ""] += 1
                home = e.team_id == self.match.home_team_id
                assist = [r for r in e.related_event_ids if by_id[r].event_type is EventType.PASS]
                out.append(
                    _Draft(
                        e.period,
                        e.clock_ms,
                        1,
                        InsightType.KEY_MOMENT,
                        "goal",
                        InsightSubject(team_id=e.team_id, player_id=e.player_id),
                        {
                            "xg": round(shot_xg(e), 2),
                            "minute": _minute(e.period, e.clock_ms),
                            "score_home": e.score_home + int(home),
                            "score_away": e.score_away + int(not home),
                        },
                        (e.event_id, *assist),
                    )
                )
            elif e.event_type is EventType.CARD and e.outcome in SENDING_OFF and e.team_id:
                out += self._sending_off(engine, e)
            elif e.event_type is EventType.FOUL and e.outcome is Outcome.PENALTY and e.team_id:
                fouled = getattr(e.details, "fouled_player_id", None)
                out.append(
                    _Draft(
                        e.period,
                        e.clock_ms,
                        1,
                        InsightType.KEY_MOMENT,
                        "penalty_awarded",
                        InsightSubject(team_id=engine.other(e.team_id), player_id=fouled),
                        {"minute": _minute(e.period, e.clock_ms)},
                        (e.event_id,),
                    )
                )
        return out

    def _sending_off(self, engine: MatchEngine, card: Event) -> list[_Draft]:
        assert card.team_id is not None
        team, p = card.team_id, card.period
        home = team == self.match.home_team_id
        left = (card.players_on_pitch_home if home else card.players_on_pitch_away) - 1
        reason = card.details.reason.value if isinstance(card.details, CardDetails) else ""
        drafts = [
            _Draft(
                p,
                card.clock_ms,
                1,
                InsightType.KEY_MOMENT,
                "sending_off",
                InsightSubject(team_id=team, player_id=card.player_id),
                {"minute": _minute(p, card.clock_ms), "players_left": left},
                (card.event_id, *card.related_event_ids),
                {"card": card.outcome.value if card.outcome else "", "reason": reason},
            )
        ]
        end_ms = engine.period_end_ms(p)
        impact_ms = card.clock_ms + self.config.sending_off_impact_ms
        if end_ms is not None and impact_ms > end_ms:
            return drafts  # the half ended first
        seen = engine.events_in(p, -1, 10**9)
        if end_ms is None and (not seen or seen[-1].clock_ms < impact_ms):
            return drafts  # live stream has not reached the impact time yet
        span = self.config.sending_off_impact_ms
        start = max(0, card.clock_ms - span)
        before = engine.team_window(team, p, start, card.clock_ms)
        after = engine.team_window(team, p, card.clock_ms, impact_ms)
        drafts.append(
            _Draft(
                p,
                impact_ms,
                2,
                InsightType.KEY_MOMENT,
                "sending_off_impact",
                InsightSubject(team_id=team),
                {
                    "possession_before": _pct(before.possession_share),
                    "possession_after": _pct(after.possession_share),
                    "control_before": round(before.control),
                    "control_after": round(after.control),
                    "turnovers_after": after.turnovers,
                    "players_left": left,
                    "window_min": round(span / MIN),
                },
                (card.event_id, *after.turnover_event_ids),
                {
                    "before_window": _window(p, start, card.clock_ms),
                    "after_window": _window(p, card.clock_ms, impact_ms),
                },
            )
        )
        return drafts

    # Player form ------------------------------------------------------------
    def _player_form(self, events: Sequence[Event]) -> list[_Draft]:
        cfg = self.config
        out = []
        shots: dict[str, list[Event]] = defaultdict(list)
        cooldown: dict[str, tuple[int, int]] = {}
        for e in events:
            if e.event_type is not EventType.SHOT or e.player_id is None:
                continue
            if isinstance(e.details, ShotDetails) and e.details.shot_type is ShotType.PENALTY:
                continue  # form is judged on non-penalty shots
            history = shots[e.player_id]
            history.append(e)
            recent = [
                s
                for s in history
                if s.period == e.period and e.clock_ms - s.clock_ms < cfg.form_window_ms
            ]
            xg = sum(shot_xg(s) for s in recent)
            if (e.period, e.clock_ms) < cooldown.get(e.player_id, (0, -1)):
                continue
            if len(recent) >= cfg.form_min_shots and xg >= cfg.form_min_xg:
                cooldown[e.player_id] = (e.period, e.clock_ms + cfg.form_cooldown_ms)
                out.append(
                    _Draft(
                        e.period,
                        e.clock_ms,
                        3,
                        InsightType.PLAYER_FORM,
                        "player_form",
                        InsightSubject(team_id=e.team_id, player_id=e.player_id),
                        {
                            "shots": len(recent),
                            "npxg": round(xg, 2),
                            "on_target": sum(
                                s.outcome in (Outcome.GOAL, Outcome.SAVED) for s in recent
                            ),
                            "goals": sum(s.outcome is Outcome.GOAL for s in recent),
                            "window_min": round(cfg.form_window_ms / MIN),
                        },
                        tuple(s.event_id for s in recent),
                        {
                            "window": _window(
                                e.period, max(0, e.clock_ms - cfg.form_window_ms), e.clock_ms
                            )
                        },
                    )
                )
        return out

    # Milestones -------------------------------------------------------------
    def _event_milestones(self, events: Sequence[Event]) -> list[_Draft]:
        out = []
        passes: dict[str, list[Event]] = defaultdict(list)
        goals: dict[str, list[Event]] = defaultdict(list)
        for e in events:
            if e.player_id is None:
                continue
            if is_counted_pass(e):
                passes[e.player_id].append(e)
                done = sum(x.outcome is Outcome.COMPLETE for x in passes[e.player_id])
                if e.outcome is Outcome.COMPLETE and done in self.config.pass_milestones:
                    tried = len(passes[e.player_id])
                    out.append(
                        _Draft(
                            e.period,
                            e.clock_ms,
                            4,
                            InsightType.MILESTONE,
                            "pass_milestone",
                            InsightSubject(team_id=e.team_id, player_id=e.player_id),
                            {
                                "completed_passes": done,
                                "attempted_passes": tried,
                                "pass_accuracy": _pct(done / tried),
                            },
                            tuple(x.event_id for x in passes[e.player_id]),
                        )
                    )
            if e.event_type is EventType.SHOT and e.outcome is Outcome.GOAL:
                goals[e.player_id].append(e)
                n = len(goals[e.player_id])
                if n in (2, 3):
                    out.append(
                        _Draft(
                            e.period,
                            e.clock_ms,
                            4,
                            InsightType.MILESTONE,
                            "brace" if n == 2 else "hat_trick",
                            InsightSubject(team_id=e.team_id, player_id=e.player_id),
                            {"goals": n},
                            tuple(x.event_id for x in goals[e.player_id]),
                        )
                    )
        return out

    # Speed and quality --------------------------------------------------------
    def _shot_speed(self, events: Sequence[Event]) -> list[_Draft]:
        out = []
        for e in events:
            if (
                e.event_type is EventType.SHOT
                and shot_speed_kmh(e) > self.config.shot_speed_alert_kmh
            ):
                out.append(
                    _Draft(
                        e.period,
                        e.clock_ms,
                        5,
                        InsightType.SPEED_ALERT,
                        "shot_speed",
                        InsightSubject(team_id=e.team_id, player_id=e.player_id),
                        {"shot_speed_kmh": round(shot_speed_kmh(e), 1)},
                        (e.event_id,),
                        auto_approve=True,
                    )
                )
        return out

    def _pass_quality(self, events: Sequence[Event]) -> list[_Draft]:
        cfg = self.config
        out = []
        cooldown: dict[str, tuple[int, int]] = {}
        for i, e in enumerate(events):
            if not is_counted_pass(e) or e.outcome is not Outcome.COMPLETE or e.team_id is None:
                continue
            difficulty = pass_difficulty_of(e)
            forward = pass_forward_m(e)
            if (
                difficulty < cfg.pass_quality_min_difficulty
                or forward < cfg.pass_quality_min_forward_m
            ):
                continue
            if (e.period, e.clock_ms) < cooldown.get(e.team_id, (0, -1)):
                continue
            cooldown[e.team_id] = (e.period, e.clock_ms + cfg.pass_quality_cooldown_ms)
            speed = pass_ball_speed_kmh(e)
            metrics: dict[str, int | float] = {
                "difficulty": round(difficulty),
                "length_m": round(pass_length_m(e), 1),
                "forward_m": round(forward, 1),
            }
            if speed is not None:
                metrics["ball_speed_kmh"] = round(speed, 1)
            receipt = events[i + 1].event_id if i + 1 < len(events) else None
            evidence = (e.event_id,) + ((receipt,) if receipt else ())
            out.append(
                _Draft(
                    e.period,
                    e.clock_ms,
                    6,
                    InsightType.PASS_QUALITY,
                    "pass_quality",
                    InsightSubject(team_id=e.team_id, player_id=e.player_id),
                    metrics,
                    evidence,
                )
            )
        return out

    # Recaps ---------------------------------------------------------------------
    def _recaps(self, engine: MatchEngine) -> list[_Draft]:
        out = []
        for e in engine.events:
            if e.event_type is not EventType.PERIOD_END:
                continue
            upto = [x for x in engine.events if (x.period, x.clock_ms) <= (e.period, e.clock_ms)]
            metrics: dict[str, int | float] = {
                "score_home": e.score_home,
                "score_away": e.score_away,
            }
            evidence: list[str] = []
            for side, team in (
                ("home", self.match.home_team_id),
                ("away", self.match.away_team_id),
            ):
                shots = [x for x in upto if x.event_type is EventType.SHOT and x.team_id == team]
                passes = [x for x in upto if is_counted_pass(x) and x.team_id == team]
                live = sum(
                    engine.possession_ms(p, 0, engine.period_end_ms(p) or 0)[team]
                    for p in range(1, e.period + 1)
                )
                total = sum(
                    sum(engine.possession_ms(p, 0, engine.period_end_ms(p) or 0).values())
                    for p in range(1, e.period + 1)
                )
                done = sum(x.outcome is Outcome.COMPLETE for x in passes)
                metrics |= {
                    f"shots_{side}": len(shots),
                    f"xg_{side}": round(sum(shot_xg(x) for x in shots), 2),
                    f"possession_{side}": _pct(live / total if total else 0.5),
                    f"passes_{side}": len(passes),
                    f"pass_accuracy_{side}": _pct(done / len(passes) if passes else None),
                }
                evidence += [x.event_id for x in shots]
            rule = "half_time_recap" if e.period == 1 else "full_time_recap"
            out.append(
                _Draft(
                    e.period,
                    e.clock_ms,
                    9,
                    InsightType.RECAP,
                    rule,
                    InsightSubject(),
                    metrics,
                    tuple(sorted(evidence)),
                )
            )
        return out

    # Tracking ---------------------------------------------------------------------
    def _tracking_rules(self, tracking: Sequence[TrackingSample]) -> list[_Draft]:
        cfg = self.config
        out = []
        dist: dict[str, float] = defaultdict(float)
        last: dict[tuple[str, int], TrackingSample] = {}
        hit_km: dict[str, set[float]] = defaultdict(set)
        fast: set[str] = set()
        for s in tracking:
            prev = last.get((s.player_id, s.period))
            last[(s.player_id, s.period)] = s
            team = s.player_id[:3]
            if prev is not None and s.clock_ms - prev.clock_ms == 1000:
                dist[s.player_id] += math.hypot(s.x - prev.x, s.y - prev.y)
            for km in cfg.distance_milestones_km:
                if dist[s.player_id] >= km * 1000 and km not in hit_km[s.player_id]:
                    hit_km[s.player_id].add(km)
                    out.append(
                        _Draft(
                            s.period,
                            s.clock_ms,
                            7,
                            InsightType.MILESTONE,
                            "distance_milestone",
                            InsightSubject(team_id=team, player_id=s.player_id),
                            {"distance_km": round(dist[s.player_id] / 1000, 1)},
                            (),
                            {"source": "tracking"},
                        )
                    )
            if s.speed_kmh >= cfg.top_speed_alert_kmh and s.player_id not in fast:
                fast.add(s.player_id)
                out.append(
                    _Draft(
                        s.period,
                        s.clock_ms,
                        7,
                        InsightType.SPEED_ALERT,
                        "top_speed",
                        InsightSubject(team_id=team, player_id=s.player_id),
                        {"top_speed_kmh": round(s.speed_kmh, 1)},
                        (),
                        {"source": "tracking", "hsr_threshold_kmh": HSR_LOW_KMH},
                        auto_approve=True,
                    )
                )
        return out


def detect(
    match: Match,
    events: Sequence[Event],
    tracking: Sequence[TrackingSample] = (),
    config: DetectorConfig | None = None,
) -> list[Trigger]:
    """Run every detector rule over a match."""
    return Detector(match, config).detect(events, tracking)
