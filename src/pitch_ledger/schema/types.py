"""Shared constrained field types: IDs and UTC timestamps."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, PlainSerializer, StringConstraints

MatchId = Annotated[str, StringConstraints(pattern=r"^M\d{4}$")]
TeamId = Annotated[str, StringConstraints(pattern=r"^T\d{2}$")]
PlayerId = Annotated[str, StringConstraints(pattern=r"^T\d{2}-P\d{2}$")]
EventId = Annotated[str, StringConstraints(pattern=r"^M\d{4}-E\d{6}$")]
InsightId = Annotated[str, StringConstraints(pattern=r"^M\d{4}-I\d{4}$")]
OverlayId = Annotated[str, StringConstraints(pattern=r"^M\d{4}-O\d{4}$")]
Bcp47 = Annotated[str, StringConstraints(pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")]


def format_utc(value: datetime) -> str:
    """Format as ISO 8601 UTC with millisecond precision, e.g. 2026-10-24T15:16:03.400Z."""
    value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


UtcTimestamp = Annotated[
    AwareDatetime,
    AfterValidator(lambda v: v.astimezone(UTC)),
    PlainSerializer(format_utc, return_type=str),
]
"""Timezone-aware timestamp, normalised to UTC and serialised with milliseconds."""


def event_id(match_id: str, seq_no: int) -> str:
    """Build an event ID: match ID + ``-E`` + 6-digit sequence number."""
    return f"{match_id}-E{seq_no:06d}"


def player_id(team_id: str, shirt_number: int) -> str:
    """Build a player ID: team ID + ``-P`` + 2-digit shirt number."""
    return f"{team_id}-P{shirt_number:02d}"
