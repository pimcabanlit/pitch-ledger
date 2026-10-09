"""Translator: each personalized version into the other demo languages, numbers unchanged."""

from pitch_ledger.agents import copy, prompts
from pitch_ledger.agents.contracts import Facts, TranslateRequest
from pitch_ledger.agents.copy_check import CheckedCopy, checked_copy
from pitch_ledger.agents.model_manager import ModelManager


async def translate(
    models: ModelManager,
    key: str,
    mode: str,
    language: str,
    text: str,
    metrics: dict[str, int | float],
    facts: Facts,
) -> CheckedCopy:
    request = TranslateRequest(
        copy_key=key, mode=mode, language=language, text=text, metrics=metrics, facts=facts
    )
    template = copy.mode_text(key, mode, language, {**metrics, **facts.names()})
    return await checked_copy(
        models, "translator", prompts.TRANSLATOR, request, metrics, language, template
    )
