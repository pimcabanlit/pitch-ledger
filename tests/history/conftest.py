"""Two finished demo matches in memory: M0001 (matchday 1) and M0002 (matchday 2)."""

import pytest

from pitch_ledger.matchlab.build import build_match
from pitch_ledger.matchlab.generator import DEFAULT_KICKOFF, MatchSpec, SimulatedMatch
from pitch_ledger.matchlab.scenarios import load_manifest
from pitch_ledger.schema import TrackingSample

Built = tuple[SimulatedMatch, list[TrackingSample]]


def demo(match_id: str) -> Built:
    entry = next(e for e in load_manifest() if e.match_id == match_id)
    return build_match(MatchSpec(
        match_id=entry.match_id, home_team_id=entry.home_team_id,
        away_team_id=entry.away_team_id, seed=entry.seed, scenario_id=entry.scenario_id.value,
        kickoff_utc=entry.kickoff or DEFAULT_KICKOFF,
    ))  # fmt: skip


@pytest.fixture(scope="session")
def m0001() -> Built:
    return demo("M0001")


@pytest.fixture(scope="session")
def m0002() -> Built:
    return demo("M0002")
