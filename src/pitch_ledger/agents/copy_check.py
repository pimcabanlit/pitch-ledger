"""Model copy with code checks: retry the failing copies once, then fall back per copy.

Every copy must use only metric numbers (the number check) and meet the editorial rules
for on-screen text (length, no clichés or unsupported claims). One model call writes a
set of copies (all modes, or all languages of one mode); copies that fail are asked for
again on their own, once; any still failing use the template text, which is built from the
same code-computed numbers.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel

from pitch_ledger.agents.contracts import CopySet
from pitch_ledger.agents.editorial import editorial_issues, mode_issues
from pitch_ledger.agents.model_manager import ModelManager, ModelUnavailable, Role
from pitch_ledger.validation.insight_rules import unsupported_numbers

ATTEMPTS = 2


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
    problems += editorial_issues(text, "overlay", language)
    return problems + (mode_issues(text, mode) if mode else [])


async def checked_set(
    models: ModelManager,
    role: Role,
    instructions: str,
    request_for: Callable[[list[str]], BaseModel],
    languages: Mapping[str, str],
    metrics: Mapping[str, float],
    templates: Mapping[str, str],
    modes: Mapping[str, str] | None = None,
) -> dict[str, CheckedCopy]:
    """Copies for every key of ``languages`` (key -> its language), checked one by one
    (``modes``: key -> its audience mode, for the per-mode depth rules)."""
    findings: dict[str, list[str]] = {k: [] for k in languages}
    done: dict[str, CheckedCopy] = {}
    pending = list(languages)
    for _ in range(ATTEMPTS):
        if not pending:
            break
        try:
            reply = await models.complete(role, instructions, request_for(pending), CopySet)
        except ModelUnavailable as exc:
            for k in pending:
                findings[k].append(f"model unavailable: {exc}")
            break
        failing = []
        for k in pending:
            text = (reply.texts.get(k) or "").strip()
            problems = copy_problems(text, metrics, languages[k], (modes or {}).get(k))
            if problems:
                findings[k].append("; ".join(problems))
                failing.append(k)
            else:
                done[k] = CheckedCopy(text, "model", findings[k])
        pending = failing
    for k in pending:
        done[k] = CheckedCopy(templates[k], "template", findings[k])
    return {k: done[k] for k in languages}
