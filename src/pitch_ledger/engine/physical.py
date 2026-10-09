"""Player physical metrics from 1 Hz tracking (docs/event-schema.md, "Player physical").

Each second's movement is the straight line from the previous sample, and its speed is
that sample's ``speed_kmh``. High-speed running is distance covered at 19.8-25.2 km/h;
a sprint is an entry above 25.2 km/h.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

from pitch_ledger.schema import TrackingSample

HSR_LOW_KMH = 19.8
SPRINT_KMH = 25.2


@dataclass
class PlayerPhysical:
    player_id: str
    seconds: int = 0
    distance_m: float = 0.0
    hsr_m: float = 0.0
    sprint_m: float = 0.0
    sprints: int = 0
    top_speed_kmh: float = 0.0

    @property
    def distance_km(self) -> float:
        return self.distance_m / 1000


def physical(
    tracking: Iterable[TrackingSample],
    period: int | None = None,
    start_ms: int = -1,
    end_ms: int = 10**9,
) -> dict[str, PlayerPhysical]:
    """Per-player physical output, optionally limited to ``(start_ms, end_ms]`` of a period.

    A second counts toward a window when its sample (the end of the movement) lies in it.
    """
    out: dict[str, PlayerPhysical] = {}
    last: dict[tuple[str, int], TrackingSample] = {}
    for s in tracking:
        key = (s.player_id, s.period)
        prev = last.get(key)
        last[key] = s
        if period is not None and s.period != period:
            continue
        if not start_ms < s.clock_ms <= end_ms:
            continue
        stats = out.setdefault(s.player_id, PlayerPhysical(s.player_id))
        stats.seconds += 1
        stats.top_speed_kmh = max(stats.top_speed_kmh, s.speed_kmh)
        if prev is None or s.clock_ms - prev.clock_ms != 1000:
            continue
        step = math.hypot(s.x - prev.x, s.y - prev.y)
        stats.distance_m += step
        if HSR_LOW_KMH <= s.speed_kmh <= SPRINT_KMH:
            stats.hsr_m += step
        elif s.speed_kmh > SPRINT_KMH:
            stats.sprint_m += step
            if prev.speed_kmh <= SPRINT_KMH:
                stats.sprints += 1
    return out
