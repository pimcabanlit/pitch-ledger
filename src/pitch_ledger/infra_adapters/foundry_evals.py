"""Publish evaluation rows to Microsoft Foundry evaluations (the portal's Evaluations page).

One row per aired insight (``scorecard.evaluation_rows``). Foundry runs the graders:

- **code checks** as ``string_check`` graders on the row's "pass"/"fail" fields: the
  checks themselves ran in Python (recompute from raw rows, numbers in the text, evidence,
  overlays, house style), so Foundry only records them next to the judges; no model, free;
- **judges**: Foundry's built-in ``groundedness`` (is the text supported by the
  code-computed metrics and names it was given?), ``coherence`` and ``fluency``, run on
  the strong deployment (about 1,600 tokens per row each);
- **safety** (optional): the built-in violence, hate and unfairness, sexual and self-harm
  evaluators.

The latest offline scorecard's gates travel as run metadata, so the run also shows the
detection and planted-error results.

This adapter calls the Foundry evaluation service through the project's OpenAI-compatible
client; it makes no model calls of its own (agent model calls stay in
``agents/model_manager.py``). Sign-in: Microsoft Entra ID; the identity needs Foundry User
on the project.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pitch_ledger.scorecard import CHECKS

EVAL_NAME = "pitch-ledger-insights"
JUDGES: dict[str, tuple[str, ...]] = {
    "groundedness": ("query", "context", "response"),
    "coherence": ("query", "response"),
    "fluency": ("response",),
}
SAFETY = ("violence", "hate_unfairness", "sexual", "self_harm")
TERMINAL = ("completed", "failed", "canceled")
MAX_METADATA = 16  # the Evals API's limit on metadata keys (values up to 512 characters)


@dataclass
class EvalResult:
    eval_id: str
    run_id: str
    status: str
    report_url: str | None
    rows: int
    criteria: dict[str, dict[str, float | int | None]] = field(default_factory=dict)
    error: str | None = None

    def code_checks_passed(self) -> bool:
        return all(self.criteria.get(name, {}).get("failed", 0) == 0 for name in CHECKS)


def item_schema(rows: Sequence[dict[str, str]]) -> dict[str, Any]:
    keys = sorted({k for row in rows for k in row})
    return {"type": "object", "properties": {k: {"type": "string"} for k in keys},
            "required": keys}  # fmt: skip


def criteria_for(deployment: str, judges: bool = True, safety: bool = False) -> list[Any]:
    """Code checks always; the judges and safety evaluators when asked."""
    criteria: list[Any] = [
        {"type": "string_check", "name": name, "input": f"{{{{item.{name}}}}}",
         "reference": "pass", "operation": "eq"}
        for name in CHECKS
    ]  # fmt: skip
    if judges:
        criteria += [
            {"type": "azure_ai_evaluator", "name": name, "evaluator_name": f"builtin.{name}",
             "initialization_parameters": {"deployment_name": deployment},
             "data_mapping": {k: f"{{{{item.{k}}}}}" for k in fields}}
            for name, fields in JUDGES.items()
        ]  # fmt: skip
    if safety:
        criteria += [
            {"type": "azure_ai_evaluator", "name": name, "evaluator_name": f"builtin.{name}",
             "data_mapping": {"query": "{{item.query}}", "response": "{{item.response}}"}}
            for name in SAFETY
        ]  # fmt: skip
    return criteria


def eval_name(judges: bool, safety: bool) -> str:
    """One evaluation per set of criteria, so runs within it compare like with like."""
    return EVAL_NAME + ("" if judges else "-code") + ("-safety" if safety else "")


def scorecard_metadata(card: dict[str, Any] | None, source: str) -> dict[str, str]:
    """The run's metadata: where the rows came from and the scorecard gates."""
    meta = {"source": source[:512]}
    if card:
        meta["scorecard"] = f"{card.get('generated_at', '?')} passed={card.get('passed')}"
        for n, (gate, ok) in enumerate(card.get("gates", {}).items(), start=1):
            if len(meta) < MAX_METADATA:
                meta[f"gate_{n}"] = f"{'PASS' if ok else 'FAIL'}: {gate}"[:512]
    return meta


def open_client(endpoint: str) -> Any:
    """The Foundry project's OpenAI-compatible client (Entra ID sign-in)."""
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    return project.get_openai_client()


def _find_or_create(client: Any, name: str, rows: Sequence[dict[str, str]],
                    criteria: list[Any]) -> str:  # fmt: skip
    for existing in client.evals.list(limit=100):
        if existing.name == name:
            return str(existing.id)
    created = client.evals.create(
        name=name,
        data_source_config={"type": "custom", "item_schema": item_schema(rows),
                            "include_sample_schema": False},
        testing_criteria=criteria,
    )  # fmt: skip
    return str(created.id)


def _summarise(client: Any, eval_id: str, run_id: str) -> dict[str, dict[str, float | int | None]]:
    passed: dict[str, int] = {}
    failed: dict[str, int] = {}
    scores: dict[str, list[float]] = {}
    for item in client.evals.runs.output_items.list(run_id=run_id, eval_id=eval_id):
        for result in item.results:
            r = result if isinstance(result, dict) else result.model_dump()
            name = str(r.get("name"))
            ok = bool(r.get("passed"))
            (passed if ok else failed)[name] = (passed if ok else failed).get(name, 0) + 1
            if isinstance(r.get("score"), int | float):
                scores.setdefault(name, []).append(float(r["score"]))
    return {
        name: {"passed": passed.get(name, 0), "failed": failed.get(name, 0),
               "mean_score": round(sum(s) / len(s), 2) if (s := scores.get(name)) else None}
        for name in sorted(set(passed) | set(failed))
    }  # fmt: skip


def publish(
    rows: Sequence[dict[str, str]],
    client: Any,
    deployment: str,
    *,
    judges: bool = True,
    safety: bool = False,
    run_name: str = "insights",
    metadata: dict[str, str] | None = None,
    poll_s: float = 5.0,
    timeout_s: float = 1800.0,
    sleep: Any = time.sleep,
) -> EvalResult:
    """Run the rows through the evaluation in Foundry and wait for the result."""
    if not rows:
        raise ValueError("no aired insights to evaluate")
    criteria = criteria_for(deployment, judges, safety)
    eval_id = _find_or_create(client, eval_name(judges, safety), rows, criteria)
    run = client.evals.runs.create(
        eval_id=eval_id,
        name=run_name,
        metadata=metadata or {},
        data_source={"type": "jsonl", "source": {"type": "file_content",
                                                 "content": [{"item": row} for row in rows]}},
    )  # fmt: skip
    waited = 0.0
    while run.status not in TERMINAL and waited < timeout_s:
        sleep(poll_s)
        waited += poll_s
        run = client.evals.runs.retrieve(run_id=run.id, eval_id=eval_id)
    error = getattr(run, "error", None)
    return EvalResult(
        eval_id=eval_id,
        run_id=str(run.id),
        status=str(run.status),
        report_url=getattr(run, "report_url", None),
        rows=len(rows),
        criteria=_summarise(client, eval_id, str(run.id)) if run.status == "completed" else {},
        error=str(getattr(error, "message", error)) if error else None,
    )
