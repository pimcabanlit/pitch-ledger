"""Momentum detection by accumulating evidence (a one-sided CUSUM per team).

A 5-minute control score swings 40-60 points by chance (each window holds only a few
possessions), so a fixed threshold on window-to-window changes cannot separate a real
shift from noise. Instead, each team's play is read as a stream of evidence items:

- every pass: did it complete less often than the pass-difficulty model expects?
  The alternative hypothesis lowers its completion probability by ``pass_drop``;
- every pass: was it played under pressure more often than the team's running rate?
  The alternative raises that rate by ``pressure_rise`` (an opponent's press switch);
- every possession, once it ends: was it lost in the team's own half, lost elsewhere, or
  kept until a shot or stoppage? Against the team's own running rates, the alternative
  moves ``own_half_rise`` of probability to own-half losses and ``turnover_rise`` to
  other losses (a collapse raises both; an opponent's high press mostly the first).

Two scores run per team, each a sum of log-likelihood ratios floored at zero:
``build_up`` (pass outcomes and possession endings: the team's own play breaks down) and
``pressing`` (pressured passes and possession endings: the opponent squeezes it). A
momentum shift fires when either score reaches ``threshold``; the onset is the moment
that score last left zero. Scores reset at each period start and after an alert. A team
alerts at most once per period (a sustained collapse is one moment, not several) and
never within ``cooldown_ms`` of its previous alert.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from pitch_ledger.engine.metrics import is_counted_pass, pass_difficulty_of
from pitch_ledger.engine.timeline import turnovers
from pitch_ledger.schema import Event
from pitch_ledger.schema.enums import EventType, Outcome


@dataclass(frozen=True)
class MomentumConfig:
    pass_drop: float = 0.3
    pressure_rise: float = 0.25
    prior_passes: int = 20
    prior_pressured: float = 0.3
    own_half_rise: float = 0.12
    turnover_rise: float = 0.05
    threshold: float = 10.0
    prior_possessions: int = 10
    prior_shares: tuple[float, float, float] = (0.25, 0.35, 0.40)
    """Starting shares of (own-half loss, other loss, kept) before a team has history."""
    cooldown_ms: int = 15 * 60_000


@dataclass(frozen=True)
class EvidenceItem:
    """One piece of evidence about ``team_id``, known at ``period``/``clock_ms``."""

    team_id: str
    period: int
    clock_ms: int
    kind: str  # "pass" or "possession"
    llr: float
    event_id: str | None  # the pass, or the turnover's cause event


DETECTORS: dict[str, frozenset[str]] = {
    "build_up": frozenset({"pass", "possession"}),
    "pressing": frozenset({"pressure", "possession"}),
}


@dataclass(frozen=True)
class MomentumAlert:
    team_id: str
    period: int
    clock_ms: int
    onset_ms: int
    score: float
    detector: str
    items: tuple[EvidenceItem, ...]


def evidence_items(events: Sequence[Event], config: MomentumConfig) -> list[EvidenceItem]:
    """The evidence stream for both teams, in the order it becomes known."""
    lost = {(t.period, t.detected_ms, t.team_id): t for t in turnovers(events)}
    counts: dict[str, list[float]] = {}  # team -> pseudo-counts of (own-half, other, kept)
    pressured: dict[str, list[float]] = {}  # team -> pseudo-counts of (pressured, passes)
    items: list[EvidenceItem] = []
    prev: Event | None = None
    for event in events:
        changed = prev is not None and event.possession_team_id != prev.possession_team_id
        if prev is not None and changed and event.period == prev.period:
            team = prev.possession_team_id
            turnover = lost.get((event.period, event.clock_ms, team))
            category = 2 if turnover is None else 0 if turnover.own_half else 1
            items.append(
                _possession_item(
                    team,
                    event,
                    category,
                    turnover.event_id if turnover else None,
                    counts,
                    config,
                )
            )
        if is_counted_pass(event) and event.team_id is not None:
            p = 1.0 - pass_difficulty_of(event) / 100.0
            q = max(0.02, p - config.pass_drop)
            ok = event.outcome is Outcome.COMPLETE
            llr = math.log(q / p) if ok else math.log((1 - q) / (1 - p))
            items.append(
                EvidenceItem(
                    event.team_id, event.period, event.clock_ms, "pass", llr, event.event_id
                )
            )
            items.append(_pressure_item(event, event.team_id, pressured, config))
        prev = None if event.event_type is EventType.PERIOD_END else event
    return items


def _pressure_item(
    event: Event, team: str, seen: dict[str, list[float]], config: MomentumConfig
) -> EvidenceItem:
    """Was this pass played under pressure more often than the team's running rate?"""
    hits, total = seen.setdefault(
        team, [config.prior_passes * config.prior_pressured, float(config.prior_passes)]
    )
    pi0 = hits / total
    pi1 = min(0.95, pi0 + config.pressure_rise)
    llr = math.log(pi1 / pi0) if event.under_pressure else math.log((1 - pi1) / (1 - pi0))
    seen[team] = [hits + event.under_pressure, total + 1]
    return EvidenceItem(team, event.period, event.clock_ms, "pressure", llr, event.event_id)


def _possession_item(
    team: str,
    at: Event,
    category: int,
    cause_id: str | None,
    counts: dict[str, list[float]],
    config: MomentumConfig,
) -> EvidenceItem:
    """Categorical evidence: 0 own-half loss, 1 other loss, 2 kept."""
    seen = counts.setdefault(team, [config.prior_possessions * s for s in config.prior_shares])
    total = sum(seen)
    p0 = [c / total for c in seen]
    p1 = [
        min(0.95, p0[0] + config.own_half_rise),
        min(0.95, p0[1] + config.turnover_rise),
        max(0.02, p0[2] - config.own_half_rise - config.turnover_rise),
    ]
    norm = sum(p1)
    llr = math.log(p1[category] / norm / p0[category])
    seen[category] += 1
    return EvidenceItem(team, at.period, at.clock_ms, "possession", llr, cause_id)


def momentum_alerts(
    events: Sequence[Event], teams: Sequence[str], config: MomentumConfig | None = None
) -> list[MomentumAlert]:
    """Run both change detectors for each team over the stream."""
    config = config or MomentumConfig()
    keys = [(team, name) for team in teams for name in DETECTORS]
    alerts: list[MomentumAlert] = []
    score = dict.fromkeys(keys, 0.0)
    onset: dict[tuple[str, str], int] = {}
    since: dict[tuple[str, str], list[EvidenceItem]] = {k: [] for k in keys}
    blocked_until: dict[str, tuple[int, int]] = {}
    alerted: set[tuple[str, int]] = set()
    period = 0
    for item in evidence_items(events, config):
        if item.period != period:
            period = item.period
            score = dict.fromkeys(keys, 0.0)
        team = item.team_id
        if team not in teams:
            continue
        if (team, item.period) in alerted:
            continue
        if (item.period, item.clock_ms) < blocked_until.get(team, (0, -1)):
            continue  # cooling down after an alert: evidence starts afresh afterwards
        for name, kinds in DETECTORS.items():
            if item.kind not in kinds:
                continue
            key = (team, name)
            if score[key] == 0.0:
                onset[key] = item.clock_ms
                since[key] = []
            score[key] = max(0.0, score[key] + item.llr)
            if score[key] > 0.0:
                since[key].append(item)
            if score[key] >= config.threshold:
                alerts.append(
                    MomentumAlert(
                        team,
                        item.period,
                        item.clock_ms,
                        onset[key],
                        score[key],
                        name,
                        tuple(since[key]),
                    )
                )
                blocked_until[team] = (item.period, item.clock_ms + config.cooldown_ms)
                alerted.add((team, item.period))
                for other in DETECTORS:
                    score[(team, other)] = 0.0
                break
    return alerts
