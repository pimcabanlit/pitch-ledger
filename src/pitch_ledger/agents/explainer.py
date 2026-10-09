"""Explainer and Narrator: turn a trigger into an insight draft through the model manager."""

import re

from pitch_ledger.agents import copy, prompts
from pitch_ledger.agents.contracts import ExplainRequest, InsightDraft
from pitch_ledger.agents.model_manager import ModelManager
from pitch_ledger.agents.offline import respond

COUNT_METRICS = (
    "turnovers_after",
    "own_half_turnovers_after",
    "shots",
    "players_left",
    "on_target",
    "passes_after",
)
"""Counts an injected error inflates (doubles), in order of preference."""


async def draft_insight(models: ModelManager, request: ExplainRequest) -> InsightDraft:
    """Ask the Explainer (or the Narrator, for recaps) for a draft."""
    if request.task == "narrate":
        return await models.complete("narrator", prompts.NARRATOR, request, InsightDraft)
    return await models.complete("explainer", prompts.EXPLAINER, request, InsightDraft)


def template_draft(request: ExplainRequest) -> InsightDraft:
    """The template draft used when no model reply is usable (same as the offline model)."""
    return InsightDraft.model_validate_json(respond(request.model_dump(mode="json")))


def plant_error(draft: InsightDraft, metrics: dict[str, int | float]) -> tuple[InsightDraft, str]:
    """Inflate one count in the draft's text (doubling it): an over-reaching stat for the
    Auditor to catch. Returns the tampered draft and what was changed."""
    for key in COUNT_METRICS:
        if key not in draft.metric_keys or key not in metrics:
            continue
        value = int(metrics[key])
        written = re.compile(rf"(?<![\d.,]){value}(?![\d.,])")
        if written.search(draft.explanation):
            wrong = value * 2 if value else 3
            text = written.sub(str(wrong), draft.explanation, count=1)
            tampered = draft.model_copy(update={"explanation": text})
            return tampered, f"{key}: {value} written as {wrong}"
    text = f"{draft.explanation} That makes 9 turnovers in a row."
    return draft.model_copy(update={"explanation": text}), "added an unsupported '9 turnovers'"


def copy_key_for(request: ExplainRequest) -> str:
    return copy.copy_key(request.trigger)
