"""Build a complete match (events and tracking) for a spec, planting its scenario."""

from pathlib import Path

from pitch_ledger.matchlab.generator import MatchSpec, SimulatedMatch, simulate_match
from pitch_ledger.matchlab.scenarios import load_scenario, plan_for
from pitch_ledger.matchlab.tracking import generate_tracking
from pitch_ledger.schema import Scenario, TrackingSample


def build_match(
    spec: MatchSpec, scenario: Scenario | None = None, scenarios_dir: Path | None = None
) -> tuple[SimulatedMatch, list[TrackingSample]]:
    """Simulate ``spec`` with its planted scenario (loaded by ID if not given)."""
    if scenario is None:
        scenario = load_scenario(spec.scenario_id, scenarios_dir)
    plan = plan_for(scenario, spec.home_team_id, spec.away_team_id)
    sim = simulate_match(spec, plan.modifiers, plan.send_offs, plan.protected)
    return sim, generate_tracking(sim, plan.modifiers)
