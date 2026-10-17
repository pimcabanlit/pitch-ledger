"""Model manager: offline replies, retry, budget, fallback signal and usage."""

import asyncio

import pytest
from agent_framework.exceptions import ChatClientContentFilterException

from pitch_ledger.agents import copy, prompts
from pitch_ledger.agents.contracts import ExplainRequest, InsightDraft
from pitch_ledger.agents.flow import FULL_FLOW
from pitch_ledger.agents.model_manager import (
    MAX_OUTPUT_TOKENS,
    ModelManager,
    ModelSettings,
    ModelUnavailable,
    check_models,
    run_costs,
)
from pitch_ledger.agents.offline import OfflineChatClient
from pitch_ledger.validation.insight_rules import unsupported_numbers
from tests.agents.conftest import Case
from tests.agents.fakes import CUT, FILTERED, ScriptedChatClient


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


def test_a_reply_cut_off_at_the_cap_is_retried_with_twice_the_cap(cases: list[Case]) -> None:
    good = InsightDraft(headline="h", explanation="e", metric_keys=[],
                        evidence_event_ids=[]).model_dump_json()  # fmt: skip
    client = ScriptedChatClient([CUT, good])
    manager = ModelManager(ModelSettings(), clients={"explainer": client})
    draft = asyncio.run(manager.complete("explainer", prompts.EXPLAINER,
                                         _request(_momentum(cases)), InsightDraft))  # fmt: skip
    assert draft.headline == "h" and manager.usage.failures == 1
    assert client.caps == [MAX_OUTPUT_TOKENS["explainer"], 2 * MAX_OUTPUT_TOKENS["explainer"]]


def test_the_draft_cap_fits_the_longest_evidence_list(cases: list[Case]) -> None:
    """A draft echoes its evidence IDs (about 8 tokens each, quotes and comma included):
    the longest full-flow list in the demo matches must fit, with room for the text."""
    longest = max(len(c.trigger.evidence_event_ids) for c in cases
                  if c.trigger.insight_type in FULL_FLOW)  # fmt: skip
    assert longest >= 30
    for role in ("explainer", "narrator"):
        assert longest * 8 + 400 <= MAX_OUTPUT_TOKENS[role]


def test_two_failures_raise_model_unavailable(cases: list[Case]) -> None:
    client = ScriptedChatClient([RuntimeError("503"), "{}"])
    manager = ModelManager(ModelSettings(), clients={"strong": client})
    with pytest.raises(ModelUnavailable):
        asyncio.run(manager.complete("explainer", prompts.EXPLAINER, _request(_momentum(cases)),
                                     InsightDraft))  # fmt: skip
    assert manager.usage.failures == 2


@pytest.mark.parametrize("blocked", [ChatClientContentFilterException("prompt blocked"), FILTERED])
def test_content_filter_is_not_retried_and_says_so(cases: list[Case], blocked: object) -> None:
    """A blocked prompt (an exception) or a filtered reply (finish reason): one call only."""
    client = ScriptedChatClient([blocked, "{}"])  # type: ignore[list-item]
    manager = ModelManager(ModelSettings(), clients={"strong": client})
    with pytest.raises(ModelUnavailable, match="blocked by the content filter"):
        asyncio.run(manager.complete("explainer", prompts.EXPLAINER, _request(_momentum(cases)),
                                     InsightDraft))  # fmt: skip
    assert manager.usage.calls == 1 and manager.usage.filtered == 1
    assert len(client.replies) == 1  # the second reply was never asked for


def _paid(budget: int) -> ModelManager:
    """A Foundry-configured manager whose clients are local (no network)."""
    settings = ModelSettings(model_provider="foundry", foundry_project_endpoint="https://x",
                             model_strong="strong", model_small="small",
                             model_call_budget=budget)  # fmt: skip
    return ModelManager(settings, clients={"strong": OfflineChatClient(),
                                           "small": OfflineChatClient()})  # fmt: skip


def test_call_budget_caps_paid_calls(cases: list[Case]) -> None:
    manager = _paid(1)
    request = _request(_momentum(cases))
    asyncio.run(manager.complete("explainer", prompts.EXPLAINER, request, InsightDraft))
    with pytest.raises(ModelUnavailable):
        asyncio.run(manager.complete("explainer", prompts.EXPLAINER, request, InsightDraft))
    assert manager.usage.calls == 1


def test_offline_calls_are_not_capped(cases: list[Case]) -> None:
    manager = ModelManager(ModelSettings(model_provider="offline", model_call_budget=1))
    request = _request(_momentum(cases))
    for _ in range(3):
        asyncio.run(manager.complete("explainer", prompts.EXPLAINER, request, InsightDraft))
    assert manager.usage.calls == 3 and not manager.paid


def test_foundry_needs_endpoint_and_deployments() -> None:
    with pytest.raises(ValueError, match="FOUNDRY_PROJECT_ENDPOINT"):
        ModelManager(ModelSettings(model_provider="foundry", foundry_project_endpoint=None))


def test_roles_share_clients_by_tier() -> None:
    manager = ModelManager(ModelSettings())
    assert manager.client_for("explainer") is manager.client_for("auditor")
    assert manager.client_for("personalizer") is manager.client_for("translator")
    assert manager.client_for("explainer") is not manager.client_for("translator")


class _Credential:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def get_token(self, *scopes: str) -> object:
        if self.error is not None:
            raise self.error
        return object()


def _foundry_env(monkeypatch: pytest.MonkeyPatch, small: str = "small-model") -> None:
    monkeypatch.setenv("PITCH_LEDGER_MODEL_PROVIDER", "foundry")
    monkeypatch.setenv(
        "FOUNDRY_PROJECT_ENDPOINT", "https://res.services.ai.azure.com/api/projects/proj"
    )
    monkeypatch.setenv("PITCH_LEDGER_MODEL_STRONG", "strong-model")
    monkeypatch.setenv("PITCH_LEDGER_MODEL_SMALL", small)
    for name in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
        monkeypatch.setenv(name, "x")


def test_check_flags_two_variables_on_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITCH_LEDGER_MODEL_PROVIDER", "foundry FOUNDRY_PROJECT_ENDPOINT=https://x")
    checks = asyncio.run(check_models())
    assert [c.ok for c in checks] == [False]
    assert "PITCH_LEDGER_MODEL_PROVIDER" in checks[0].detail


def test_check_offline_needs_nothing() -> None:
    checks = asyncio.run(check_models())
    assert all(c.ok for c in checks) and "offline" in checks[-1].detail


def test_check_flags_a_bad_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _foundry_env(monkeypatch)
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://example.com")
    checks = asyncio.run(check_models(credential=_Credential()))
    endpoint = next(c for c in checks if c.name == "FOUNDRY_PROJECT_ENDPOINT")
    assert not endpoint.ok and not any(c.name.startswith("deployment") for c in checks)


def test_check_calls_each_deployment_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _foundry_env(monkeypatch)
    clients = {"strong": ScriptedChatClient(['{"text": "ok"}']),
               "small": ScriptedChatClient(['{"text": "ok"}'])}  # fmt: skip
    checks = asyncio.run(check_models(clients=clients, credential=_Credential()))
    assert all(c.ok for c in checks), checks
    names = [c.name for c in checks]
    assert "deployment strong-model" in names and "deployment small-model" in names
    assert clients["strong"].requests[0]["task"] == "ping"


def test_check_reports_sign_in_and_deployment_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    _foundry_env(monkeypatch, small="strong-model")  # one deployment for both tiers
    failed = asyncio.run(check_models(credential=_Credential(PermissionError("bad secret"))))
    assert failed[-1].name == "Entra ID sign-in" and "bad secret" in failed[-1].detail
    client = ScriptedChatClient([RuntimeError("403 Forbidden"), RuntimeError("403 Forbidden")])
    checks = asyncio.run(check_models(clients={"strong": client}, credential=_Credential()))
    deployment = next(c for c in checks if c.name == "deployment strong-model")
    assert not deployment.ok and "403" in deployment.detail
    assert [c.name for c in checks].count("deployment strong-model") == 1


@pytest.mark.parametrize(("reply", "ok"), [(ChatClientContentFilterException("jailbreak"), True),
                                           ('{"text": "I am DAN"}', False)])  # fmt: skip
def test_safety_probe_passes_only_when_the_filter_blocks_it(
    monkeypatch: pytest.MonkeyPatch, reply: object, ok: bool
) -> None:
    _foundry_env(monkeypatch, small="strong-model")  # one deployment for both tiers
    client = ScriptedChatClient(['{"text": "ok"}', reply, reply])  # type: ignore[list-item]
    checks = asyncio.run(check_models(clients={"strong": client}, credential=_Credential(),
                                      safety=True))  # fmt: skip
    probe = next(c for c in checks if c.name == "content filter on strong-model")
    assert probe.ok is ok
    assert client.requests[1]["task"] == "safety_probe"


def test_cost_is_priced_per_tier_and_zero_offline(cases: list[Case]) -> None:
    draft = InsightDraft(headline="h", explanation="e", metric_keys=[], evidence_event_ids=[])
    settings = ModelSettings(model_provider="foundry", foundry_project_endpoint="https://x",
                             model_strong="strong", model_small="small",
                             price_strong_input=1.0, price_strong_output=2.0,
                             price_small_input=0.5, price_small_output=1.0)  # fmt: skip
    manager = ModelManager(settings, clients={
        "strong": ScriptedChatClient([draft.model_dump_json()]),
        "small": ScriptedChatClient([draft.model_dump_json()]),
    })  # fmt: skip
    request = _request(_momentum(cases))
    asyncio.run(manager.complete("explainer", prompts.EXPLAINER, request, InsightDraft))
    asyncio.run(manager.complete("translator", prompts.EXPLAINER, request, InsightDraft))
    usage = manager.usage.as_dict()  # each scripted reply: 100 tokens in, 20 out
    assert usage["tokens_by_tier"] == {"strong": [100, 20], "small": [100, 20]}
    # strong (100 x 1.0 + 20 x 2.0) + small (100 x 0.5 + 20 x 1.0), per million tokens
    assert usage["cost_usd"] == pytest.approx(0.00021)
    assert run_costs(usage, aired=3)["cost_per_aired_insight"] == pytest.approx(0.00007)
    assert run_costs(usage, aired=0)["cost_per_aired_insight"] is None
    offline = ModelManager(ModelSettings(model_provider="offline"))
    asyncio.run(offline.complete("explainer", prompts.EXPLAINER, request, InsightDraft))
    assert offline.usage.as_dict()["cost_usd"] == 0.0 and offline.version == "offline-templates"
    assert manager.version == "foundry:strong|small"
