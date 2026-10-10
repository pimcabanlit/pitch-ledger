"""Translator: one mode's English copy into every other demo language, in one model call;
numbers unchanged."""

from collections.abc import Sequence

from pitch_ledger.agents import copy, prompts
from pitch_ledger.agents.contracts import Facts, TranslateRequest
from pitch_ledger.agents.copy_check import CheckedCopy, checked_set
from pitch_ledger.agents.model_manager import ModelManager


async def translate(
    models: ModelManager,
    key: str,
    mode: str,
    languages: Sequence[str],
    text: str,
    metrics: dict[str, int | float],
    facts: Facts,
) -> dict[str, CheckedCopy]:
    """Copy per language; each one checked, retried alone once, else the template."""
    values = {**metrics, **facts.names()}

    def request(wanted: list[str]) -> TranslateRequest:
        return TranslateRequest(
            copy_key=key, mode=mode, languages=wanted, text=text, metrics=metrics, facts=facts
        )

    return await checked_set(
        models,
        "translator",
        prompts.TRANSLATOR,
        request,
        {lang: lang for lang in languages},
        metrics,
        {lang: copy.mode_text(key, mode, lang, values) for lang in languages},
        {lang: mode for lang in languages},
    )
