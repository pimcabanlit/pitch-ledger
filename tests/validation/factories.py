"""A small, hand-built valid match used to test each validation rule."""

from datetime import UTC, datetime, timedelta
from typing import Any

from pitch_ledger.schema import Match, Player
from pitch_ledger.schema.enums import NO_LOCATION_TYPES, EventType

HOME, AWAY = "T01", "T02"
KICKOFF = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)
POSITIONS = ["gk", "cb", "cb", "fb", "fb", "cm", "cm", "dm", "st", "am", "wg", "cm", "st", "cb"]


def make_match() -> Match:
    return Match(
        match_id="M0001", competition="Harbour Coast League", season="2026-27",
        kickoff_utc=KICKOFF, venue="Test Ground", home_team_id=HOME, away_team_id=AWAY,
        referee="A. Referee", scenario_id="none", generator_version="test", random_seed=1,
    )  # fmt: skip


def make_players() -> list[Player]:
    players = []
    for team in (HOME, AWAY):
        for shirt, position in enumerate(POSITIONS, start=1):
            players.append(
                Player.model_validate(
                    {"player_id": f"{team}-P{shirt:02d}", "team_id": team,
                     "name": f"Player {team} {shirt}", "display_name": f"P. {shirt}",
                     "shirt_number": shirt, "position": position, "preferred_foot": "right",
                     "is_starter": shirt <= 11, "top_speed_kmh": 32.0,
                     "ratings": {"pace": 70, "passing": 70, "finishing": 70, "pressing": 70}}
                )
            )  # fmt: skip
    return players


class Builder:
    """Appends raw event rows, keeping seq, clock, possession and score bookkeeping."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.period = 1
        self.clock = 0
        self.poss_id = 0
        self.poss_team: str | None = None
        self.pattern = "from_kickoff"
        self.score = [0, 0]

    def start_period(self, period: int, kickoff_team: str) -> dict[str, Any]:
        self.period, self.clock = period, 0
        return self.add("period_start", poss=kickoff_team, pattern="from_kickoff", dt=0)

    def add(
        self,
        event_type: str,
        team: str | None = None,
        player: str | None = None,
        *,
        poss: str | None = None,
        pattern: str | None = None,
        dt: int = 2000,
        **fields: Any,
    ) -> dict[str, Any]:
        if poss is not None and poss != self.poss_team:
            self.poss_id += 1
            self.poss_team = poss
        if pattern is not None:
            self.pattern = pattern
        self.clock += dt
        seq = len(self.rows) + 1
        offset = timedelta(minutes=0 if self.period == 1 else 63, milliseconds=self.clock)
        row: dict[str, Any] = {
            "event_id": f"M0001-E{seq:06d}", "match_id": "M0001", "seq_no": seq,
            "period": self.period, "clock_ms": self.clock,
            "feed_ts_utc": (KICKOFF + offset).isoformat(), "team_id": team,
            "player_id": player, "event_type": event_type,
            "possession_id": self.poss_id, "possession_team_id": self.poss_team,
            "play_pattern": self.pattern, "score_home": self.score[0],
            "score_away": self.score[1], "players_on_pitch_home": 11,
            "players_on_pitch_away": 11,
        }  # fmt: skip
        if EventType(event_type) not in NO_LOCATION_TYPES:
            row |= {"start_x": 50.0, "start_y": 34.0, "end_x": 55.0, "end_y": 34.0}
        row |= fields
        self.rows.append(row)
        return row

    def pass_(
        self, team: str, player: str, receiver: str, outcome: str = "complete",
        set_piece: str = "none", pass_type: str = "short", duration_ms: int = 800, **kw: Any,
    ) -> dict[str, Any]:  # fmt: skip
        row = self.add(
            "pass", team, player, outcome=outcome, receiver_player_id=receiver,
            duration_ms=duration_ms,
            details={"pass_type": pass_type, "height": "ground", "set_piece": set_piece}, **kw,
        )  # fmt: skip
        if outcome == "complete":
            self.add("ball_receipt", team, receiver, outcome="success", dt=800,
                     related_event_ids=[row["event_id"]])  # fmt: skip
        return row

    def goal(self, team: str, player: str, assist: dict[str, Any]) -> dict[str, Any]:
        row = self.add(
            "shot", team, player, outcome="goal", duration_ms=300,
            start_x=94.0, start_y=34.0, end_x=105.0, end_y=34.0,
            related_event_ids=[assist["event_id"]],
            details={"shot_type": "open_play", "shot_speed_kmh": 95.0, "on_target": True,
                     "goalkeeper_id": f"{AWAY if team == HOME else HOME}-P01",
                     "assist_event_id": assist["event_id"], "assist_type": "through_ball"},
        )  # fmt: skip
        self.score[0 if team == HOME else 1] += 1
        return row


def baseline_rows() -> list[dict[str, Any]]:
    """A short two-period match that passes every event rule."""
    b = Builder()
    b.start_period(1, HOME)
    b.pass_(HOME, "T01-P09", "T01-P08", set_piece="kick_off")
    through = b.pass_(HOME, "T01-P08", "T01-P10", pass_type="through_ball", pattern="regular_play")
    b.goal(HOME, "T01-P10", through)
    receipt = b.pass_(AWAY, "T02-P09", "T02-P08", set_piece="kick_off", poss=AWAY,
                      pattern="from_kickoff")  # fmt: skip
    b.add("pressure", HOME, "T01-P06", outcome="no_effect", duration_ms=900,
          related_event_ids=[receipt["event_id"]],
          details={"target_player_id": "T02-P08", "counterpress": False})  # fmt: skip
    b.add("foul", HOME, "T01-P04", outcome="free_kick", details={"fouled_player_id": "T02-P08"})
    b.add("card", HOME, "T01-P04", outcome="yellow", details={"reason": "foul"})
    b.pass_(AWAY, "T02-P08", "T02-P10", outcome="incomplete", set_piece="free_kick",
            pattern="from_free_kick")  # fmt: skip
    b.add("interception", HOME, "T01-P05", outcome="won", poss=HOME, pattern="regular_play",
          details={"opponent_player_id": "T02-P10"})  # fmt: skip
    b.pass_(HOME, "T01-P05", "T01-P07", outcome="out_of_play", end_y=68.0, duration_ms=1500)
    b.add("out_of_play", outcome="throw_in")
    b.pass_(AWAY, "T02-P03", "T02-P06", set_piece="throw_in", poss=AWAY, pattern="from_throw_in")
    b.add("period_end")
    b.start_period(2, HOME)
    b.pass_(HOME, "T01-P09", "T01-P06", set_piece="kick_off")
    b.add("substitution", AWAY, "T02-P11", outcome="success",
          details={"player_in_id": "T02-P12", "player_out_id": "T02-P11"})  # fmt: skip
    b.add("tackle", AWAY, "T02-P12", outcome="won", poss=AWAY, pattern="regular_play",
          details={"opponent_player_id": "T01-P06"})  # fmt: skip
    b.add("carry", AWAY, "T02-P12", outcome="success", duration_ms=2000)
    b.add("shot", AWAY, "T02-P12", outcome="saved", duration_ms=400,
          details={"shot_type": "open_play", "shot_speed_kmh": 88.0, "on_target": True,
                   "goalkeeper_id": "T01-P01"})  # fmt: skip
    b.add("goalkeeper", HOME, "T01-P01", outcome="catch", poss=HOME,
          details={"action": "catch"})  # fmt: skip
    b.add("period_end")
    return b.rows


def find(rows: list[dict[str, Any]], event_type: str, nth: int = 0) -> dict[str, Any]:
    """The nth row of a given type."""
    return [r for r in rows if r["event_type"] == event_type][nth]
