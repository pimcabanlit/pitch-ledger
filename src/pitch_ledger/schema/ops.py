"""The ``ops alert``: a pipeline problem the producer should know about (not a match event).

Raised by the live service (feed gaps, feed lag, validation), after each trigger (blocked
insights, template fallbacks, dead letters) and kept in the ledger; the producer
acknowledges it in the Studio console.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from pitch_ledger.schema.types import MatchId

AlertId = Annotated[str, StringConstraints(pattern=r"^M\d{4}-X\d{4}$")]

AlertKind = Literal[
    "feed_gap",  # no feed message for a while during play
    "feed_lag",  # p95 send-to-receive latency too high over a minute
    "validation_reject",  # rows rejected by the ingest rules (dropped before the engine)
    "validation_flag",  # rows passed with a warning, summarized per minute
    "insight_blocked",  # the Auditor blocked a draft after its last round
    "trigger_dead_lettered",  # a trigger failed every delivery attempt
    "model_fallback",  # template copy used, or the model call budget is spent
]
Severity = Literal["info", "warning", "critical"]


class OpsAlert(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    alert_id: AlertId
    match_id: MatchId
    kind: AlertKind
    severity: Severity
    message: Annotated[str, Field(max_length=500)]
    refs: list[str] = Field(default_factory=list)  # event, trigger or insight IDs
    raised_at: datetime
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
