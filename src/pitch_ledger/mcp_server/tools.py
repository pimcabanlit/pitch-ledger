"""Read-only stats tools over one match directory.

Every number an agent can cite is reached through these tools (or the trigger it was
handed); none of them can change data. The MCP server exposes them to agents, and the
Auditor recomputes through them.
"""

from dataclasses import asdict
from functools import cached_property
from pathlib import Path
from typing import Any

from pitch_ledger.engine import MatchEngine, physical, shot_xg
from pitch_ledger.engine.detector import detect
from pitch_ledger.engine.metrics import is_counted_pass
from pitch_ledger.engine.recompute import recompute_trigger
from pitch_ledger.engine.timeline import turnovers
from pitch_ledger.matchlab.io import TRIGGERS_JSONL, read_match, read_triggers
from pitch_ledger.schema import Event, TrackingSample, Trigger
from pitch_ledger.schema.enums import EventType, Outcome

MAX_EVENTS = 300


class StatsTools:
    """Tool implementations for a match directory (loaded lazily, cached)."""

    def __init__(self, match_dir: Path | str) -> None:
        self.match_dir = Path(match_dir)

    @cached_property
    def _files(self) -> Any:
        return read_match(self.match_dir)

    @cached_property
    def events(self) -> list[Event]:
        return [Event.model_validate(e) for e in self._files.events]

    @cached_property
    def tracking(self) -> list[TrackingSample]:
        return [TrackingSample.model_validate(s) for s in self._files.tracking]

    @cached_property
    def engine(self) -> MatchEngine:
        return MatchEngine(self._files.match, self.events)

    @cached_property
    def triggers(self) -> dict[str, Trigger]:
        if (self.match_dir / TRIGGERS_JSONL).exists():
            found = read_triggers(self.match_dir)
        else:
            found = detect(self._files.match, self.events, self.tracking)
        return {t.trigger_id: t for t in found}

    # Tools -------------------------------------------------------------------
    def get_events(
        self,
        event_ids: list[str] | None = None,
        period: int | None = None,
        start_ms: int = -1,
        end_ms: int = 10**9,
    ) -> list[dict[str, Any]]:
        """Event rows by ID, or within ``(start_ms, end_ms]`` of a period (capped)."""
        if event_ids is not None:
            wanted = set(event_ids)
            rows = [e for e in self.events if e.event_id in wanted]
        elif period is not None:
            rows = self.engine.events_in(period, start_ms, end_ms)
        else:
            raise ValueError("give event_ids or a period window")
        return [e.model_dump(mode="json") for e in rows[:MAX_EVENTS]]

    def get_team_state(
        self, team_id: str, period: int, start_ms: int, end_ms: int
    ) -> dict[str, Any]:
        """Possession, passing, turnovers, PPDA, tempo and control for a team window."""
        if team_id not in self.engine.teams:
            raise ValueError(f"{team_id} is not in this match")
        return asdict(self.engine.team_window(team_id, period, start_ms, end_ms))

    def get_player_stats(
        self, player_id: str, period: int | None = None, start_ms: int = -1, end_ms: int = 10**9
    ) -> dict[str, Any]:
        """Passing, shooting, turnovers and running for a player (whole match or a window)."""
        rows = [
            e
            for e in self.events
            if e.player_id == player_id
            and (period is None or (e.period == period and start_ms < e.clock_ms <= end_ms))
        ]
        passes = [e for e in rows if is_counted_pass(e)]
        shots = [e for e in rows if e.event_type is EventType.SHOT]
        done = sum(e.outcome is Outcome.COMPLETE for e in passes)
        lost = [t for t in turnovers(self.events) if t.event_id in {e.event_id for e in rows}]
        run = physical(
            [s for s in self.tracking if s.player_id == player_id], period, start_ms, end_ms
        ).get(player_id)
        return {
            "player_id": player_id,
            "passes": len(passes),
            "passes_completed": done,
            "pass_accuracy": round(100 * done / len(passes), 1) if passes else None,
            "shots": len(shots),
            "xg": round(sum(shot_xg(s) for s in shots), 2),
            "goals": sum(s.outcome is Outcome.GOAL for s in shots),
            "turnovers": len(lost),
            "distance_km": round(run.distance_km, 2) if run else None,
            "hsr_m": round(run.hsr_m) if run else None,
            "sprints": run.sprints if run else None,
            "top_speed_kmh": run.top_speed_kmh if run else None,
        }

    def get_trigger(self, trigger_id: str) -> dict[str, Any]:
        """A detected moment with its metrics, evidence and windows."""
        return self.triggers[trigger_id].model_dump(mode="json")

    def recompute_trigger(self, trigger_id: str) -> dict[str, int | float]:
        """Every metric of a trigger recomputed from raw rows up to its moment."""
        trigger = self.triggers[trigger_id]
        return recompute_trigger(trigger, self._files.match, self.events, self.tracking)

    def recompute_metric(self, trigger_id: str, metric: str) -> dict[str, Any]:
        """One metric recomputed, next to the value the trigger states."""
        fresh = self.recompute_trigger(trigger_id)
        stated = self.triggers[trigger_id].metrics.get(metric)
        return {
            "trigger_id": trigger_id,
            "metric": metric,
            "value": fresh.get(metric),
            "stated": stated,
            "matches": fresh.get(metric) == stated,
        }
