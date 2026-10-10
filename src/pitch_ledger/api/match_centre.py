"""Match Centre data: a match replayed on a clock in the browser.

**Broadcast frame.** Stored coordinates face each team's own attack (docs/event-schema.md,
Conventions). The Match Centre draws one fixed pitch, as a camera on the touchline sees it:
the home team attacks toward x = 105 in period 1 and toward x = 0 in period 2. Only
positions are transformed here; football numbers come from the engine, unchanged.

Built once per match file and cached (a few matches; keyed by file modification time), so
replaying a match does not re-read 15 MB of tracking per request.
"""

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from pitch_ledger.matchlab.io import EVENTS_JSONL, TRACKING_JSONL, MatchFiles, read_match

PITCH_X, PITCH_Y = 105.0, 68.0
KEY_EVENTS = {"shot", "card", "substitution", "period_start", "period_end"}
MAX_WINDOW_S = 300
CACHE_SIZE = 3


class TimelineEvent(BaseModel):
    seq: int
    period: int
    clock_ms: int
    type: str
    outcome: str | None
    team_id: str | None
    player_id: str | None
    receiver_id: str | None
    x: float | None  # broadcast frame (m)
    y: float | None
    end_x: float | None
    end_y: float | None
    score_home: int  # after this event: a goal counts at the shot, not at the restart
    score_away: int
    key: bool  # shown in the feed: shots, cards, substitutions, period markers
    player_in_id: str | None = None  # substitutions


class PeriodSpan(BaseModel):
    period: int
    end_ms: int


class Timeline(BaseModel):
    match_id: str
    home_team_id: str
    away_team_id: str
    periods: list[PeriodSpan]
    events: list[TimelineEvent]


class TrackingWindow(BaseModel):
    period: int
    from_s: int
    to_s: int
    players: list[str]
    frames: list[list[float | None]]  # per second: x, y for each player in order (null: off)


def flips(team_id: str | None, period: int, away_team_id: str) -> bool:
    """True when a team's stored frame is mirrored in the broadcast frame."""
    return (team_id == away_team_id) != (period == 2)


def to_display(x: float | None, y: float | None, flip: bool) -> tuple[float | None, float | None]:
    if x is None or y is None:
        return x, y
    return (round(PITCH_X - x, 1), round(PITCH_Y - y, 1)) if flip else (round(x, 1), round(y, 1))


@dataclass
class MatchData:
    files: MatchFiles
    tracking_path: Path
    timeline: Timeline
    _frames: dict[int, tuple[list[str], dict[int, list[float | None]]]] = field(
        default_factory=dict
    )

    def frames(self, period: int) -> tuple[list[str], dict[int, list[float | None]]]:
        """Per-second frames of one period, read from the tracking file on first use."""
        if not self._frames:
            self._frames = build_frames(self.tracking_path, self.files)
        return self._frames.get(period, ([], {}))

    def tracking_window(self, period: int, from_s: int, to_s: int) -> TrackingWindow:
        to_s = min(to_s, from_s + MAX_WINDOW_S)
        players, frames = self.frames(period)
        empty: list[float | None] = [None] * (2 * len(players))
        rows = [frames.get(s, empty) for s in range(max(0, from_s), to_s + 1)]
        return TrackingWindow(period=period, from_s=max(0, from_s), to_s=to_s, players=players,
                              frames=rows)  # fmt: skip


def build_timeline(files: MatchFiles) -> Timeline:
    away = files.match.away_team_id
    events = []
    ends: dict[int, int] = {}
    rows = files.events
    for i, e in enumerate(rows):
        goal = e["event_type"] == "shot" and e["outcome"] == "goal"
        after = rows[i + 1] if goal and i + 1 < len(rows) else e  # the score the goal produced
        flip = flips(e["team_id"], e["period"], away)
        x, y = to_display(e["start_x"], e["start_y"], flip)
        end_x, end_y = to_display(e["end_x"], e["end_y"], flip)
        details = e.get("details") or {}
        events.append(TimelineEvent(
            seq=e["seq_no"], period=e["period"], clock_ms=e["clock_ms"], type=e["event_type"],
            outcome=e["outcome"], team_id=e["team_id"], player_id=e["player_id"],
            receiver_id=e.get("receiver_player_id"), x=x, y=y, end_x=end_x, end_y=end_y,
            score_home=after["score_home"], score_away=after["score_away"],
            key=e["event_type"] in KEY_EVENTS,
            player_in_id=details.get("player_in_id") if isinstance(details, dict) else None,
        ))  # fmt: skip
        if e["event_type"] == "period_end":
            ends[e["period"]] = e["clock_ms"]
    last: dict[int, int] = {}
    for e in files.events:  # a match still being fed has no period end yet: use its last event
        last[e["period"]] = max(last.get(e["period"], 0), e["clock_ms"])
    spans = {**last, **ends}
    return Timeline(
        match_id=files.match.match_id,
        home_team_id=files.match.home_team_id,
        away_team_id=away,
        periods=[PeriodSpan(period=p, end_ms=spans[p]) for p in sorted(spans)],
        events=events,
    )


def build_frames(
    path: Path, files: MatchFiles
) -> dict[int, tuple[list[str], dict[int, list[float | None]]]]:
    """Tracking as per-second frames in the broadcast frame, players in squad order."""
    away = files.match.away_team_id
    team_of = {p.player_id: p.team_id for p in files.players}
    order = [p.player_id for p in sorted(files.players, key=lambda p: (p.team_id, p.shirt_number))]
    found: dict[int, dict[int, dict[str, tuple[float | None, float | None]]]] = {}
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row: dict[str, Any] = json.loads(line)
                period, second = row["period"], row["clock_ms"] // 1000
                flip = flips(team_of.get(row["player_id"]), period, away)
                found.setdefault(period, {}).setdefault(second, {})[row["player_id"]] = to_display(
                    row["x"], row["y"], flip
                )
    result = {}
    for period, seconds in found.items():
        present = {pid for frame in seconds.values() for pid in frame}
        players = [pid for pid in order if pid in present]
        frames = {}
        for second, frame in seconds.items():
            flat: list[float | None] = []
            for pid in players:
                flat.extend(frame.get(pid, (None, None)))
            frames[second] = flat
        result[period] = (players, frames)
    return result


class MatchCentre:
    """Cached Match Centre data per match directory."""

    def __init__(self, size: int = CACHE_SIZE) -> None:
        self.size = size
        self._cache: OrderedDict[tuple[Path, float], MatchData] = OrderedDict()

    def get(self, match_dir: Path) -> MatchData:
        events = match_dir / EVENTS_JSONL
        key = (match_dir, events.stat().st_mtime if events.exists() else 0.0)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        files = read_match(match_dir, with_tracking=False)
        data = MatchData(files, match_dir / TRACKING_JSONL, build_timeline(files))
        self._cache[key] = data
        while len(self._cache) > self.size:
            self._cache.popitem(last=False)
        return data
