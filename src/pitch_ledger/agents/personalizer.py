"""Personalizer: one approved insight rewritten for each audience mode (English)."""

from pitch_ledger.agents import copy, prompts
from pitch_ledger.agents.contracts import Facts, PersonalizeRequest
from pitch_ledger.agents.copy_check import CheckedCopy, checked_copy
from pitch_ledger.agents.model_manager import ModelManager
from pitch_ledger.schema import Insight


async def personalize(
    models: ModelManager,
    key: str,
    mode: str,
    insight: Insight,
    metrics: dict[str, int | float],
    facts: Facts,
) -> CheckedCopy:
    request = PersonalizeRequest(
        copy_key=key,
        mode=mode,
        headline=insight.headline,
        explanation=insight.explanation,
        metrics=metrics,
        facts=facts,
    )
    template = copy.mode_text(key, mode, "en", {**metrics, **facts.names()})
    return await checked_copy(
        models, "personalizer", prompts.PERSONALIZER, request, metrics, "en", template
    )
