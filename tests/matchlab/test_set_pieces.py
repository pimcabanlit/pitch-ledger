"""Set-piece shapes put players where the Laws of the Game allow."""

import math

import pytest

from pitch_ledger.core.pitch import flip
from pitch_ledger.matchlab.set_pieces import (
    Restart,
    enforce_laws,
    set_piece_position,
)
from pitch_ledger.matchlab.shape import FORMATION_SLOTS
from pitch_ledger.schema.enums import StyleProfile

STYLE = StyleProfile.POSSESSION


def _in_area_or_arc(x: float, y: float) -> bool:
    return (x > 88.5 and 13.84 < y < 54.16) or math.hypot(x - 94, y - 34) < 9.15


@pytest.mark.parametrize("formation", sorted(FORMATION_SLOTS))
def test_penalty_shape(formation: str) -> None:
    pen = Restart("penalty", "T01", 0, 15_000, (94.0, 34.0), "T01-P09")
    for slot in range(11):
        mine = set_piece_position(pen, "T01", formation, slot, STYLE)  # taking frame already
        assert not _in_area_or_arc(*mine)
        theirs = flip(*set_piece_position(pen, "T02", formation, slot, STYLE))
        if slot == 0:
            assert theirs[0] > 104 and 30 < theirs[1] < 38  # keeper on the goal line
        else:
            assert not _in_area_or_arc(*theirs)


@pytest.mark.parametrize("formation", sorted(FORMATION_SLOTS))
def test_kick_off_shape(formation: str) -> None:
    ko = Restart("kick_off", "T01", 0, 60_000, (52.5, 34.0), "T01-P09")
    for slot in range(11):
        assert set_piece_position(ko, "T01", formation, slot, STYLE)[0] < 52.5
        x, y = set_piece_position(ko, "T02", formation, slot, STYLE)
        assert x < 52.5 and math.hypot(x - 52.5, y - 34) > 9.15


@pytest.mark.parametrize(("kind", "ball"), [("corner", (104.5, 0.5)), ("free_kick", (80.0, 40.0)),
                                            ("free_kick", (45.0, 20.0))])  # fmt: skip
def test_opponents_keep_their_distance(kind: str, ball: tuple[float, float]) -> None:
    restart = Restart(kind, "T01", 0, 20_000, ball, "T01-P07")
    for formation in FORMATION_SLOTS:
        for slot in range(1, 11):
            x, y = flip(*set_piece_position(restart, "T02", formation, slot, STYLE))
            assert math.hypot(x - ball[0], y - ball[1]) >= 9.1


def test_enforce_laws_pulls_wanderers_back() -> None:
    pen = Restart("penalty", "T01", 0, 15_000, (94.0, 34.0), "T01-P09")
    assert enforce_laws(pen, "T01", 9, "T01-P10", (95.0, 40.0))[0] <= 87.5  # out of the area
    assert enforce_laws(pen, "T01", 9, "T01-P09", (94.0, 34.0)) == (94.0, 34.0)  # the taker
    keeper = enforce_laws(pen, "T02", 0, "T02-P01", (3.0, 34.0))  # their own frame
    assert keeper[0] == pytest.approx(0.3)
    fk = Restart("free_kick", "T01", 0, 15_000, (60.0, 30.0), "T01-P06")
    x, y = flip(*enforce_laws(fk, "T02", 4, "T02-P05", flip(62.0, 31.0)))
    assert math.hypot(x - 60, y - 30) == pytest.approx(9.15, abs=0.01)
