"""The committed league file is complete, consistent and stays fixed."""

import hashlib
from collections import Counter
from importlib import resources

from pitch_ledger.matchlab.roster import load_league
from pitch_ledger.schema.enums import Position, StyleProfile

FORMATION_SIZES = {"4-3-3", "4-2-3-1", "4-4-2", "5-4-1"}
BANNED = ("premier", "league one", "united kingdom", "fc barcelona", "real madrid")


def test_league_has_enough_clubs_and_every_style() -> None:
    league = load_league()
    assert len(league.teams) >= 4
    assert {c.style_profile for c in league.teams} == set(StyleProfile)
    assert len({c.team_id for c in league.teams}) == len(league.teams)


def test_squads_are_full_and_ids_consistent() -> None:
    for club in load_league().teams:
        assert len(club.players) >= 22
        counts = Counter(p.position for p in club.players)
        assert counts[Position.GK] >= 2
        shirts = [p.shirt_number for p in club.players]
        assert len(set(shirts)) == len(shirts)
        for p in club.players:
            assert p.team_id == club.team_id
            assert p.player_id == f"{club.team_id}-P{p.shirt_number:02d}"
        assert club.formation in FORMATION_SIZES


def test_matchday_squad_has_11_starters_and_one_keeper_starting() -> None:
    for club in load_league().teams:
        squad = club.matchday_squad()
        starters = [p for p in squad if p.is_starter]
        assert len(squad) == 20 and len(starters) == 11
        assert [p.position for p in starters].count(Position.GK) == 1
        assert squad[0].position is Position.GK
        assert any(p.position is Position.GK for p in squad if not p.is_starter)


def test_names_unique_and_free_of_trademarks() -> None:
    league = load_league()
    names = [p.name for c in league.teams for p in c.players]
    assert len(set(names)) == len(names)
    text = " ".join([league.competition, *[c.name for c in league.teams], *names]).lower()
    assert not any(b in text for b in BANNED)


def test_outfield_top_speeds_within_realism_band() -> None:
    for club in load_league().teams:
        for p in club.players:
            if p.position is not Position.GK:
                assert 28.0 <= p.top_speed_kmh <= 36.0


def test_league_file_is_frozen() -> None:
    """Names never change: update this hash only on a deliberate roster change."""
    data = resources.files("pitch_ledger.matchlab").joinpath("data/league.json").read_bytes()
    assert hashlib.sha256(data).hexdigest()[:16] == LEAGUE_SHA


LEAGUE_SHA = "73e8102dd1a64cb7"
