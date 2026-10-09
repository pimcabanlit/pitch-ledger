"""Who is on the pitch, updated from substitutions and sending-offs."""

from collections.abc import Iterable

from pitch_ledger.schema import Event, Player, SubstitutionDetails
from pitch_ledger.schema.enums import EventType, Outcome

SENDING_OFF = frozenset({Outcome.RED, Outcome.SECOND_YELLOW})


class Lineup:
    """Tracks the players on the pitch for both teams."""

    def __init__(self, players: Iterable[Player]) -> None:
        self.team_of: dict[str, str] = {}
        self.on_pitch: set[str] = set()
        self.used: set[str] = set()
        for player in players:
            self.team_of[player.player_id] = player.team_id
            if player.is_starter:
                self.on_pitch.add(player.player_id)
                self.used.add(player.player_id)

    def count(self, team_id: str) -> int:
        """Players currently on the pitch for a team."""
        return sum(1 for p in self.on_pitch if self.team_of[p] == team_id)

    def apply(self, event: Event) -> None:
        """Update after an event: substitutions swap players, sending-offs remove one."""
        if event.event_type is EventType.SUBSTITUTION and isinstance(
            event.details, SubstitutionDetails
        ):
            self.on_pitch.discard(event.details.player_out_id)
            self.on_pitch.add(event.details.player_in_id)
            self.used.add(event.details.player_in_id)
        elif event.event_type is EventType.CARD and event.outcome in SENDING_OFF:
            if event.player_id is not None:
                self.on_pitch.discard(event.player_id)
