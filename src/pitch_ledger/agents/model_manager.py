"""The single entry point for model calls (CLAUDE.md: nothing else imports a provider SDK).

- Provider: ``offline`` (deterministic copy templates, zero cost) or ``foundry``
  (Microsoft Foundry deployments through Agent Framework's ``FoundryChatClient``, signed in
  with Microsoft Entra ID via ``azure-identity``; no keys).
- Model names live here only: a stronger deployment for the Explainer, Auditor and
  Narrator, a small fast one for the Personalizer and Translator.
- Cost controls: an output-token cap per role and a call budget per run.
- Reliability: one retry on an error or an unparseable reply, then ``ModelUnavailable``
  so the caller falls back to template copy built from code-computed numbers.
"""

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from agent_framework import Agent, SupportsChatGetResponse
from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pitch_ledger.agents.offline import OfflineChatClient

Role = Literal["explainer", "auditor", "narrator", "personalizer", "translator"]
Tier = Literal["strong", "small"]

TIER: dict[Role, Tier] = {
    "explainer": "strong",
    "auditor": "strong",
    "narrator": "strong",
    "personalizer": "small",
    "translator": "small",
}
MAX_OUTPUT_TOKENS: dict[Role, int] = {
    "explainer": 400,
    "auditor": 300,
    "narrator": 500,
    "personalizer": 200,
    "translator": 250,
}

log = logging.getLogger(__name__)


class ModelSettings(BaseSettings):
    """Model configuration, from ``PITCH_LEDGER_*`` environment variables (or ``.env``)."""

    model_config = SettingsConfigDict(env_prefix="PITCH_LEDGER_", env_file=".env", extra="ignore")

    model_provider: Literal["offline", "foundry"] = "offline"
    model_strong: str | None = None
    model_small: str | None = None
    foundry_project_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PITCH_LEDGER_FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_PROJECT_ENDPOINT"
        ),
    )
    model_call_budget: int = 120
    model_timeout_s: float = 60.0
    model_temperature: float = 0.2

    def deployment(self, tier: Tier) -> str | None:
        return self.model_strong if tier == "strong" else self.model_small


class ModelUnavailable(RuntimeError):
    """No usable reply (budget spent, provider error twice, or unparseable output)."""


@dataclass
class Usage:
    calls: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_role: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "by_role": dict(self.by_role),
        }


def parse_json[T: BaseModel](text: str, model: type[T]) -> T:
    """Parse the first JSON object in ``text`` into ``model``."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    return model.model_validate(json.loads(text[start : end + 1]))


class ModelManager:
    """Creates chat clients and runs every agent call with caps, budget and retry."""

    def __init__(
        self,
        settings: ModelSettings | None = None,
        clients: dict[str, SupportsChatGetResponse[Any]] | None = None,
    ) -> None:
        """``clients`` overrides the client per role or tier (tests use scripted clients)."""
        self.settings = settings or ModelSettings()
        self.usage = Usage()
        self._overrides = dict(clients or {})
        self._clients: dict[str, SupportsChatGetResponse[Any]] = {}
        self._credential: Any = None
        if self.settings.model_provider == "foundry":
            missing = [
                name
                for name, value in (
                    ("FOUNDRY_PROJECT_ENDPOINT", self.settings.foundry_project_endpoint),
                    ("PITCH_LEDGER_MODEL_STRONG", self.settings.model_strong),
                    ("PITCH_LEDGER_MODEL_SMALL", self.settings.model_small),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"foundry provider needs {', '.join(missing)}")

    @property
    def provider(self) -> str:
        return self.settings.model_provider

    def client_for(self, role: Role) -> SupportsChatGetResponse[Any]:
        tier = TIER[role]
        for key in (role, tier):
            if key in self._overrides:
                return self._overrides[key]
        if tier not in self._clients:
            self._clients[tier] = self._make_client(tier)
        return self._clients[tier]

    def _make_client(self, tier: Tier) -> SupportsChatGetResponse[Any]:
        if self.settings.model_provider == "offline":
            return OfflineChatClient()
        from agent_framework_foundry import FoundryChatClient
        from azure.identity.aio import DefaultAzureCredential

        if self._credential is None:
            self._credential = DefaultAzureCredential()
        return FoundryChatClient(
            project_endpoint=self.settings.foundry_project_endpoint,
            model=self.settings.deployment(tier),
            credential=self._credential,
        )

    async def close(self) -> None:
        if self._credential is not None:
            await self._credential.close()
            self._credential = None

    async def __aenter__(self) -> "ModelManager":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def complete[T: BaseModel](
        self, role: Role, instructions: str, request: BaseModel, reply: type[T]
    ) -> T:
        """Run one agent call and parse its JSON reply; retries once, then gives up."""
        last: Exception | None = None
        for _ in range(2):
            if self.usage.calls >= self.settings.model_call_budget:
                raise ModelUnavailable("model call budget spent") from last
            self.usage.calls += 1
            self.usage.by_role[role] += 1
            agent = Agent(
                self.client_for(role),
                instructions=instructions,
                name=role,
                default_options={
                    "max_tokens": MAX_OUTPUT_TOKENS[role],
                    "temperature": self.settings.model_temperature,
                },
            )
            try:
                response = await asyncio.wait_for(
                    agent.run(request.model_dump_json()), self.settings.model_timeout_s
                )
                details = response.usage_details or {}
                self.usage.input_tokens += int(details.get("input_token_count") or 0)
                self.usage.output_tokens += int(details.get("output_token_count") or 0)
                return parse_json(response.text, reply)
            except Exception as exc:  # provider error, timeout or unparseable reply
                self.usage.failures += 1
                last = exc
                log.warning("%s call failed: %s", role, exc)
        raise ModelUnavailable(f"{role}: no usable reply") from last
