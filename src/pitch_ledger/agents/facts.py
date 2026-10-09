"""Build the names and context an agent may use for a trigger."""

from collections import Counter
from collections.abc import Sequence

from pitch_ledger.agents.contracts import Facts
from pitch_ledger.schema import Event, Player, Team, Trigger


def build_facts(
    trigger: Trigger,
    teams: Sequence[Team],
    players: Sequence[Player],
    home_team_id: str,
    away_team_id: str,
    events_by_id: dict[str, Event],
) -> Facts:
    """Names for the trigger's subject, its opponent and, for team moments, the subject
    team's player most involved in the evidence (for player-focus copy)."""
    team_names = {t.team_id: t.name for t in teams}
    player_names = {p.player_id: p.display_name for p in players}
    subject_team = trigger.subject.team_id
    opponent = None
    if subject_team is not None:
        opponent = away_team_id if subject_team == home_team_id else home_team_id
    opponent = str(trigger.context.get("opponent_team_id") or opponent or "") or None
    focus = None
    if trigger.subject.player_id is None and subject_team is not None:
        involved = Counter(
            e.player_id
            for i in trigger.evidence_event_ids
            if (e := events_by_id.get(i)) is not None and e.player_id and e.team_id == subject_team
        )
        if involved:
            focus = sorted(involved.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        else:
            focus = next((p.player_id for p in players if p.team_id == subject_team), None)
    return Facts(
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        team_names=team_names,
        player_names=player_names,
        subject_team_id=subject_team,
        opponent_team_id=opponent,
        subject_player_id=trigger.subject.player_id,
        focus_player_id=focus,
    )
