"""Model manager: offline replies, retry, budget, fallback signal and usage."""

import asyncio

import pytest

from pitch_ledger.agents import copy, prompts
from pitch_ledger.agents.contracts import ExplainRequest, InsightDraft
from pitch_ledger.agents.model_manager import ModelManager, ModelSettings, ModelUnavailable
from pitch_ledger.validation.insight_rules import unsupported_numbers
from tests.agents.conftest import Case
from tests.agents.fakes import ScriptedChatClient


def _request(case: Case) -> ExplainRequest:
    return ExplainRequest(copy_key=copy.copy_key(case.trigger), trigger=case.trigger,
                          facts=case.facts)  # fmt: skip


def _momentum(cases: list[Case]) -> Case:
    return next(c for c in cases if c.trigger.rule_id == "momentum_shift")


def test_offline_explainer_draft(cases: list[Case]) -> None:
    case = _momentum(cases)
    manager = ModelManager(ModelSettings(model_provider="offline"))
    draft = asyncio.run(manager.complete("explainer", prompts.EXPLAINER, _request(case),
                                         InsightDraft))  # fmt: skip
    assert draft.evidence_event_ids == case.trigger.evidence_event_ids
    assert set(draft.metric_keys) <= set(case.trigger.metrics)
    values = [case.trigger.metrics[k] for k in draft.metric_keys]
    assert unsupported_numbers(f"{draft.headline} {draft.explanation}", values) == []
    assert manager.usage.calls == 1 and manager.usage.failures == 0


def test_retry_once_then_success(cases: list[Case]) -> None:
    good = InsightDraft(headline="h", explanation="e", metric_keys=[],
                        evidence_event_ids=[]).model_dump_json()  # fmt: skip
    client = ScriptedChatClient(["not json", f"Sure! {good}"])
    manager = ModelManager(ModelSettings(), clients={"explainer": client})
    draft = asyncio.run(manager.complete("explainer", prompts.EXPLAINER,
                                         _request(_momentum(cases)), InsightDraft))  # fmt: skip
    assert draft.headline == "h"
    assert manager.usage.calls == 2 and manager.usage.failures == 1
    assert manager.usage.input_tokens == 200
    assert client.requests[0]["task"] == "explain"


def test_two_failures_raise_model_unavailable(cases: list[Case]) -> None:
    client = ScriptedChatClient([RuntimeError("503"), "{}"])
    manager = ModelManager(ModelSettings(), clients={"strong": client})
    with pytest.raises(ModelUnavailable):
        asyncio.run(manager.complete("explainer", prompts.EXPLAINER, _request(_momentum(cases)),
                                     InsightDraft))  # fmt: skip
    assert manager.usage.failures == 2


def test_call_budget(cases: list[Case]) -> None:
    manager = ModelManager(ModelSettings(model_call_budget=1))
    request = _request(_momentum(cases))
    asyncio.run(manager.complete("explainer", prompts.EXPLAINER, request, InsightDraft))
    with pytest.raises(ModelUnavailable):
        asyncio.run(manager.complete("explainer", prompts.EXPLAINER, request, InsightDraft))
    assert manager.usage.calls == 1


def test_foundry_needs_endpoint_and_deployments() -> None:
    with pytest.raises(ValueError, match="FOUNDRY_PROJECT_ENDPOINT"):
        ModelManager(ModelSettings(model_provider="foundry", foundry_project_endpoint=None))


def test_roles_share_clients_by_tier() -> None:
    manager = ModelManager(ModelSettings())
    assert manager.client_for("explainer") is manager.client_for("auditor")
    assert manager.client_for("personalizer") is manager.client_for("translator")
    assert manager.client_for("explainer") is not manager.client_for("translator")
