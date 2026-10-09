"""Stats and Momentum Engine: deterministic metrics, rolling windows and the moment detector.

Every figure an agent can cite is computed here from event and tracking rows.
"""

from pitch_ledger.engine.metrics import (
    is_counted_pass,
    pass_ball_speed_kmh,
    pass_difficulty_of,
    pass_length_m,
    shot_speed_kmh,
    shot_xg,
)
from pitch_ledger.engine.physical import PlayerPhysical, physical
from pitch_ledger.engine.timeline import PlaySpan, Turnover, play_spans, turnovers
from pitch_ledger.engine.windows import (
    WINDOW_MS,
    ChaosWindow,
    MatchEngine,
    TeamWindow,
    control_score,
    match_minute,
)

__all__ = [
    "WINDOW_MS",
    "ChaosWindow",
    "MatchEngine",
    "PlaySpan",
    "PlayerPhysical",
    "TeamWindow",
    "Turnover",
    "control_score",
    "is_counted_pass",
    "match_minute",
    "pass_ball_speed_kmh",
    "pass_difficulty_of",
    "pass_length_m",
    "physical",
    "play_spans",
    "shot_speed_kmh",
    "shot_xg",
    "turnovers",
]
