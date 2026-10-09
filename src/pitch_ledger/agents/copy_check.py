"""Model copy with a code number check: retry once, then fall back to the template."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from pitch_ledger.agents.contracts import CopyText, PersonalizeRequest, TranslateRequest
from pitch_ledger.agents.model_manager import ModelManager, ModelUnavailable, Role
from pitch_ledger.validation.insight_rules import unsupported_numbers

ATTEMPTS = 2


@dataclass
class CheckedCopy:
    text: str
    source: str  # "model" or "template"
    findings: list[str] = field(default_factory=list)


async def checked_copy(
    models: ModelManager,
    role: Role,
    instructions: str,
    request: PersonalizeRequest | TranslateRequest,
    metrics: Mapping[str, float],
    language: str,
    template_text: str,
) -> CheckedCopy:
    """Ask the model for copy whose numbers all come from ``metrics``; otherwise use the
    template text (built from the same code-computed numbers)."""
    findings: list[str] = []
    for _ in range(ATTEMPTS):
        try:
            reply = await models.complete(role, instructions, request, CopyText)
        except ModelUnavailable as exc:
            findings.append(f"model unavailable: {exc}")
            break
        bad = unsupported_numbers(reply.text, metrics.values(), language)
        if not bad and reply.text.strip():
            return CheckedCopy(reply.text.strip(), "model", findings)
        findings.append(f"numbers not in metrics: {', '.join(bad) or 'empty text'}")
    return CheckedCopy(template_text, "template", findings)
