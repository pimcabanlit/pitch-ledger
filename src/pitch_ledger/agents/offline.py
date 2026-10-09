"""Deterministic offline model: renders the copy templates instead of calling a provider.

It speaks the same JSON contracts as the real models, so the full Agent Framework
pipeline runs locally at zero cost, and its output is what production falls back to.
"""

import json
from typing import Any

from agent_framework import BaseChatClient, ChatResponse, Message, UsageDetails
from agent_framework._tools import FunctionInvocationLayer

from pitch_ledger.agents import copy
from pitch_ledger.agents.contracts import (
    CopyText,
    ExplainRequest,
    InsightDraft,
    PersonalizeRequest,
    ReviewVerdict,
    TranslateRequest,
)


def respond(payload: dict[str, Any]) -> str:
    """The offline reply (JSON text) to one request payload."""
    task = payload.get("task")
    if task in ("explain", "narrate"):
        req = ExplainRequest.model_validate(payload)
        values = {**req.trigger.metrics, **req.facts.names()}
        draft = InsightDraft(
            headline=copy.headline(req.copy_key, values),
            explanation=copy.explanation(req.copy_key, values),
            metric_keys=copy.used_metrics(req.copy_key, req.trigger.metrics),
            evidence_event_ids=list(req.trigger.evidence_event_ids),
        )
        return draft.model_dump_json()
    if task == "review":
        return ReviewVerdict(verdict="pass").model_dump_json()
    if task == "personalize":
        p = PersonalizeRequest.model_validate(payload)
        values = {**p.metrics, **p.facts.names()}
        return CopyText(text=copy.mode_text(p.copy_key, p.mode, "en", values)).model_dump_json()
    if task == "translate":
        t = TranslateRequest.model_validate(payload)
        values = {**t.metrics, **t.facts.names()}
        text = copy.mode_text(t.copy_key, t.mode, t.language, values)
        return CopyText(text=text).model_dump_json()
    raise ValueError(f"offline model cannot handle task {task!r}")


class OfflineChatClient(FunctionInvocationLayer[Any], BaseChatClient[Any]):
    """A chat client whose 'model' is the copy catalogue."""

    OTEL_PROVIDER_NAME = "pitch-ledger-offline"

    async def _inner_get_response(
        self, *, messages: Any, stream: bool, options: Any, **kwargs: Any
    ) -> Any:
        if stream:
            raise NotImplementedError("the offline model does not stream")
        payload = json.loads(messages[-1].text)
        text = respond(payload)
        usage = UsageDetails(input_token_count=0, output_token_count=0)
        return ChatResponse(
            messages=[Message(role="assistant", contents=[text])],
            model="offline",
            usage_details=usage,
        )
