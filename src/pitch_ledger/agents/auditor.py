"""Auditor: checks every number against code before an insight can air.

Deterministic checks run first, in code:
- INS-01: every number in the headline and explanation matches a cited metric at its
  written precision, and every cited metric exists on the trigger;
- evidence: cited event IDs belong to the trigger's evidence (and some are cited);
- recompute: the trigger's metrics are recomputed from raw rows through the stats tools
  (the MCP server), so a tampered or stale number fails;
- editorial (``STYLE:``): headline and explanation length, clichés, unsupported claims
  (history, causes, predictions) and repetition. A draft that fails only on style after
  the last revision airs as template copy instead of being blocked.
Only when those pass does a model review the wording (unsupported claims, tone,
neutrality). Findings go back to the Explainer, at most twice; then the insight is blocked.
"""

from dataclasses import dataclass, field

from opentelemetry import trace

from pitch_ledger.agents import prompts
from pitch_ledger.agents.contracts import Facts, InsightDraft, ReviewRequest, ReviewVerdict
from pitch_ledger.agents.editorial import draft_issues
from pitch_ledger.agents.model_manager import ModelManager, ModelUnavailable
from pitch_ledger.mcp_server.client import StatsGateway
from pitch_ledger.schema import Trigger
from pitch_ledger.validation.insight_rules import unsupported_numbers

MAX_REVISIONS = 2
_tracer = trace.get_tracer("pitch_ledger")  # a no-op unless tracing is configured


@dataclass
class AuditResult:
    passed: bool
    findings: list[str] = field(default_factory=list)
    stage: str = "check"  # "check" or "review"
    notes: list[str] = field(default_factory=list)


class Auditor:
    def __init__(
        self, models: ModelManager, stats: StatsGateway, model_review: bool = True
    ) -> None:
        self.models = models
        self.stats = stats
        self.model_review = model_review
        self._recomputed: dict[str, dict[str, int | float]] = {}

    async def recomputed(self, trigger: Trigger) -> dict[str, int | float]:
        if trigger.trigger_id not in self._recomputed:
            self._recomputed[trigger.trigger_id] = await self.stats.recompute_trigger(
                trigger.trigger_id
            )
        return self._recomputed[trigger.trigger_id]

    async def check(self, draft: InsightDraft, trigger: Trigger) -> list[str]:
        """Deterministic checks; an empty list means the numbers may air."""
        with _tracer.start_as_current_span("audit check (code)") as span:
            findings = await self._check(draft, trigger)
            span.set_attributes({"pitch_ledger.passed": not findings,
                                 "pitch_ledger.findings": findings[:5]})  # fmt: skip
            return findings

    async def _check(self, draft: InsightDraft, trigger: Trigger) -> list[str]:
        findings = []
        unknown = [k for k in draft.metric_keys if k not in trigger.metrics]
        findings += [f"metric '{k}' is not one of the trigger's metrics" for k in unknown]
        cited = {k: trigger.metrics[k] for k in draft.metric_keys if k in trigger.metrics}
        for written in unsupported_numbers(
            f"{draft.headline}\n{draft.explanation}", cited.values()
        ):
            findings.append(
                f"INS-01: '{written}' in the text matches no cited metric "
                f"(cited: {', '.join(f'{k}={v}' for k, v in cited.items()) or 'none'})"
            )
        evidence = set(trigger.evidence_event_ids)
        stray = [i for i in draft.evidence_event_ids if i not in evidence]
        findings += [f"evidence {i} is not part of this moment" for i in stray]
        if evidence and not draft.evidence_event_ids:
            findings.append("no evidence event IDs cited")
        findings += draft_issues(draft.headline, draft.explanation)
        fresh = await self.recomputed(trigger)
        for key, stated in trigger.metrics.items():
            if fresh.get(key) != stated:
                findings.append(
                    f"recompute: {key} is {fresh.get(key)} from the raw events, not {stated}"
                )
        return findings

    async def review(self, draft: InsightDraft, trigger: Trigger, facts: Facts) -> AuditResult:
        """Model review of the wording; numbers have already passed."""
        if not self.model_review:
            return AuditResult(True, stage="review", notes=["model review off"])
        request = ReviewRequest(draft=draft, metrics=trigger.metrics, facts=facts)
        try:
            verdict = await self.models.complete("auditor", prompts.AUDITOR, request, ReviewVerdict)
        except ModelUnavailable as exc:
            return AuditResult(True, stage="review", notes=[f"review unavailable: {exc}"])
        if verdict.verdict == "pass":
            return AuditResult(True, stage="review", notes=verdict.issues)
        return AuditResult(False, verdict.issues or ["reviewer asked for a revision"], "review")
