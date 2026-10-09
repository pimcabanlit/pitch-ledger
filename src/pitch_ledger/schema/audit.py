"""The ``audit record``: one step in an insight's audit trail, kept in the ledger."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from pitch_ledger.schema.types import InsightId, MatchId

AuditId = Annotated[str, StringConstraints(pattern=r"^M\d{4}-A\d{4}$")]

AuditAction = Literal[
    "draft",  # an agent produced a draft
    "inject_error",  # a deliberate wrong number planted for the demo/test
    "check",  # deterministic number, recompute and evidence checks
    "review",  # model review of claims, tone and neutrality
    "revise",  # findings sent back to the Explainer
    "approve",  # passed on the first draft
    "correct",  # passed after revision
    "block",  # never airs
    "producer_approve",
    "producer_block",
    "fallback",  # model unavailable or copy failed its check: template copy used
]


class AuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    audit_id: AuditId
    match_id: MatchId
    insight_id: InsightId
    trigger_id: str | None = None
    round: Annotated[int, Field(ge=0)]
    actor: str
    action: AuditAction
    passed: bool | None = None
    findings: list[str] = Field(default_factory=list)
    headline: str | None = None
    explanation: str | None = None
