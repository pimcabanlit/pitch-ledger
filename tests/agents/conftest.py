"""Shared fixtures: real triggers from the demo matches (events only, plus tracking once)."""

from dataclasses import dataclass

import pytest

from pitch_ledger.agents import copy
from pitch_ledger.agents.contracts import Facts
from pitch_ledger.agents.facts import build_facts
from pitch_ledger.engine.detector import detect
from pitch_ledger.matchlab.build import build_match
from pitch_ledger.matchlab.generator import MatchSpec, SimulatedMatch
from pitch_ledger.matchlab.scenarios import load_manifest
from pitch_ledger.schema import TrackingSample, Trigger


@dataclass(frozen=True)
class Case:
    sim: SimulatedMatch
    trigger: Trigger
    facts: Facts
    tracking: list[TrackingSample]


def _cases() -> list[Case]:
    out = []
    for entry in load_manifest():
        spec = MatchSpec(match_id=entry.match_id, home_team_id=entry.home_team_id,
                         away_team_id=entry.away_team_id, seed=entry.seed,
                         scenario_id=entry.scenario_id.value)  # fmt: skip
        sim, tracking = build_match(spec)
        by_id = {e.event_id: e for e in sim.events}
        for t in detect(sim.match, sim.events, tracking):
            if copy.supported(copy.copy_key(t)):
                facts = build_facts(t, sim.teams, sim.players, sim.match.home_team_id,
                                    sim.match.away_team_id, by_id)  # fmt: skip
                out.append(Case(sim, t, facts, tracking))
    return out


@pytest.fixture(scope="session")
def cases() -> list[Case]:
    return _cases()
