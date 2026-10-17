"""Personalizer: one approved insight rewritten for every audience mode (English), in one
model call."""

from collections.abc import Sequence

from pitch_ledger.agents import copy, prompts
from pitch_ledger.agents.contracts import Facts, PersonalizeRequest
from pitch_ledger.agents.copy_check import CheckedCopy, checked_set
from pitch_ledger.agents.model_manager import ModelManager
from pitch_ledger.schema import Insight


async def personalize(
    models: ModelManager,
    key: str,
    modes: Sequence[str],
    insight: Insight,
    metrics: dict[str, int | float],
    facts: Facts,
) -> dict[str, CheckedCopy]:
    """English copy per mode; each one checked, retried alone with feedback, else the template."""
    values = {**metrics, **facts.names()}

    def request(wanted: list[str], feedback: dict[str, str]) -> PersonalizeRequest:
        return PersonalizeRequest(
            copy_key=key,
            modes=wanted,
            headline=insight.headline,
            explanation=insight.explanation,
            metrics=metrics,
            facts=facts,
            feedback=feedback,
        )

    return await checked_set(
        models,
        "personalizer",
        prompts.PERSONALIZER,
        request,
        {mode: "en" for mode in modes},
        metrics,
        {mode: copy.mode_text(key, mode, "en", values) for mode in modes},
        {mode: mode for mode in modes},
    )
