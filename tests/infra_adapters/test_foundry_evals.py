"""Foundry evaluations adapter, with a fake Evals client (no Azure calls)."""

from types import SimpleNamespace
from typing import Any

import pytest

from pitch_ledger.infra_adapters.foundry_evals import (
    JUDGES,
    SAFETY,
    criteria_for,
    eval_name,
    publish,
    scorecard_metadata,
)
from pitch_ledger.scorecard import CHECKS

ROW = {"insight_id": "M0002-I0001", "query": "q", "context": "c", "response": "r",
       **dict.fromkeys(CHECKS, "pass"), "problems": ""}  # fmt: skip


class FakeRuns:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses, self.created, self.output_items = statuses, [], self

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(id="run_1", status="queued")

    def retrieve(self, run_id: str, eval_id: str) -> SimpleNamespace:
        status = self.statuses.pop(0)
        return SimpleNamespace(id=run_id, status=status, error=None,
                               report_url="https://ai.azure.com/report")  # fmt: skip

    def list(self, run_id: str, eval_id: str) -> list[SimpleNamespace]:
        results = [{"name": n, "passed": True, "score": 1.0} for n in CHECKS]
        results.append({"name": "groundedness", "passed": True, "score": 5.0})
        return [SimpleNamespace(results=results)]


class FakeEvals:
    def __init__(self, existing: list[str], statuses: list[str]) -> None:
        self.existing, self.created = existing, []
        self.runs = FakeRuns(statuses)

    def list(self, limit: int) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=f"eval_{n}", name=n) for n in self.existing]

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(id="eval_new")


def _client(existing: list[str], statuses: list[str]) -> SimpleNamespace:
    return SimpleNamespace(evals=FakeEvals(existing, statuses))


def test_criteria_are_code_checks_then_judges_then_safety() -> None:
    names = [c["name"] for c in criteria_for("gpt-4.1-mini", judges=True, safety=True)]
    assert names == [*CHECKS, *JUDGES, *SAFETY]
    code = criteria_for("gpt-4.1-mini", judges=False)
    assert [c["type"] for c in code] == ["string_check"] * len(CHECKS)
    assert code[0]["input"] == "{{item.recompute}}" and code[0]["reference"] == "pass"
    grounded = criteria_for("gpt-4.1-mini")[len(CHECKS)]
    assert grounded["evaluator_name"] == "builtin.groundedness"
    assert grounded["initialization_parameters"] == {"deployment_name": "gpt-4.1-mini"}
    assert grounded["data_mapping"]["context"] == "{{item.context}}"
    assert eval_name(True, False) == "pitch-ledger-insights"
    assert eval_name(False, True) == "pitch-ledger-insights-code-safety"


def test_publish_creates_the_eval_once_runs_inline_rows_and_summarises() -> None:
    client = _client(existing=[], statuses=["in_progress", "completed"])
    card = {"generated_at": "t", "passed": True, "gates": {"no dead letters": True}}
    meta = scorecard_metadata(card, "cosmos ledger: M0002")
    result = publish([ROW], client, "gpt-4.1-mini", metadata=meta, sleep=lambda s: None)
    assert result.status == "completed" and result.report_url == "https://ai.azure.com/report"
    assert result.code_checks_passed() and result.criteria["groundedness"]["mean_score"] == 5.0
    [created] = client.evals.created
    assert created["name"] == "pitch-ledger-insights"
    assert created["data_source_config"]["item_schema"]["required"] == sorted(ROW)
    [run] = client.evals.runs.created
    assert run["data_source"]["source"]["content"] == [{"item": ROW}]
    assert run["metadata"]["gate_1"] == "PASS: no dead letters"

    again = _client(existing=["pitch-ledger-insights"], statuses=["completed"])
    publish([ROW], again, "gpt-4.1-mini", sleep=lambda s: None)
    assert again.evals.created == []  # the existing evaluation gets a new run


def test_publish_refuses_an_empty_ledger() -> None:
    with pytest.raises(ValueError, match="no aired insights"):
        publish([], _client([], []), "gpt-4.1-mini")
