"""Model copy with code checks: retry the failing copies with feedback, then fall back per copy.

Every copy must use only metric numbers (the number check) and meet the editorial rules
for on-screen text (length, no clichés or unsupported claims). One model call writes a
set of copies (all modes, or all languages of one mode); copies that fail are asked for
again on their own, up to twice, each time with the rejected text and why it failed; any
still failing use the template text, which is built from the same code-computed numbers.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel

from pitch_ledger.agents.contracts import CopySet
from pitch_ledger.agents.editorial import editorial_issues, mode_issues
from pitch_ledger.agents.model_manager import ModelManager, ModelUnavailable, Role
from pitch_ledger.engine.season import has_season_facts
from pitch_ledger.validation.insight_rules import unsupported_numbers

ATTEMPTS = 3  # the first reply and two retries of the failing copies, with feedback


@dataclass
class CheckedCopy:
    text: str
    source: str  # "model" or "template"
    findings: list[str] = field(default_factory=list)


def copy_problems(
    text: str, metrics: Mapping[str, float], language: str, mode: str | None = None
) -> list[str]:
    """Why a copy may not air (empty list: it may)."""
    if not text.strip():
        return ["numbers not in metrics: empty text"]
    bad = unsupported_numbers(text, metrics.values(), language)
    problems = [f"numbers not in metrics: {', '.join(bad)}"] if bad else []
    problems += editorial_issues(text, "overlay", language, season=has_season_facts(metrics))
    return problems + (mode_issues(text, mode) if mode else [])


def rejected(text: str, problems: list[str]) -> str:
    """Feedback for a retry: the rejected text and what to fix."""
    return f"Rejected: {text!r}. Fix: {'; '.join(problems)}."


async def checked_set(
    models: ModelManager,
    role: Role,
    instructions: str,
    request_for: Callable[[list[str], dict[str, str]], BaseModel],
    languages: Mapping[str, str],
    metrics: Mapping[str, float],
    templates: Mapping[str, str],
    modes: Mapping[str, str] | None = None,
    problems: Callable[[str, str], list[str]] | None = None,
    max_tokens: int | None = None,
) -> dict[str, CheckedCopy]:
    """Copies for every key of ``languages`` (key -> its language), checked one by one
    (``request_for(keys, feedback)``: the request for those keys, with the feedback on
    their rejected texts; ``modes``: key -> its audience mode, for the per-mode depth rules;
    ``problems``: a check of its own, (key, text) -> why it may not air, in place of the
    on-screen one)."""
    findings: dict[str, list[str]] = {k: [] for k in languages}
    done: dict[str, CheckedCopy] = {}
    feedback: dict[str, str] = {}
    pending = list(languages)
    for _ in range(ATTEMPTS):
        if not pending:
            break
        try:
            reply = await models.complete(
                role, instructions, request_for(pending, feedback), CopySet, max_tokens
            )
        except ModelUnavailable as exc:
            for k in pending:
                findings[k].append(f"model unavailable: {exc}")
            break
        feedback = {}  # for the keys that fail this round, which are asked for again
        for k in pending:
            text = (reply.texts.get(k) or "").strip()
            found = (
                problems(k, text)
                if problems
                else copy_problems(text, metrics, languages[k], (modes or {}).get(k))
            )
            if found:
                findings[k].append("; ".join(found))
                feedback[k] = rejected(text, found)
            else:
                done[k] = CheckedCopy(text, "model", findings[k])
        pending = list(feedback)
    for k in pending:
        done[k] = CheckedCopy(templates[k], "template", findings[k])
    return {k: done[k] for k in languages}
