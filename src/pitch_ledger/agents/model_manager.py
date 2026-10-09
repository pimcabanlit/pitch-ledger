"""The single entry point for model calls (CLAUDE.md: nothing else imports a provider SDK).

- Provider: ``offline`` (deterministic copy templates, zero cost) or ``foundry``
  (Microsoft Foundry deployments through Agent Framework's ``FoundryChatClient``, signed in
  with Microsoft Entra ID via ``azure-identity``; no keys).
- Model names live here only: a stronger deployment for the Explainer, Auditor and
  Narrator, a small fast one for the Personalizer and Translator.
- Cost controls: an output-token cap per role and a call budget per run (paid providers).
- Reliability: one retry on an error or an unparseable reply, then ``ModelUnavailable``
  so the caller falls back to template copy built from code-computed numbers.
"""

import asyncio
import json
import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from agent_framework import Agent, SupportsChatGetResponse
from pydantic import AliasChoices, BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from pitch_ledger.agents.contracts import CopyText, PingRequest
from pitch_ledger.agents.offline import OfflineChatClient
from pitch_ledger.core.checks import Check, short_error

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
    model_call_budget: int = 120  # calls per run, paid providers only
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

    @property
    def paid(self) -> bool:
        """Calls cost money (the call budget applies); offline templates are free."""
        return self.provider != "offline"

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
            if self.paid and self.usage.calls >= self.settings.model_call_budget:
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


# Configuration check (``pitch-ledger check-models``) -----------------------------------

ENDPOINT_PATTERN = re.compile(r"^https://[\w-]+\.services\.ai\.azure\.com/api/projects/[\w.-]+/?$")
TOKEN_SCOPE = "https://ai.azure.com/.default"
CREDENTIAL_VARS = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
PING_INSTRUCTIONS = 'Reply with JSON only: {"text": "<reply_with>"}.'


_short = short_error


def check_settings() -> tuple[list[Check], ModelSettings | None]:
    """Settings as the model manager reads them; never prints secret values."""
    try:
        settings = ModelSettings()
    except ValidationError as exc:
        fields = ", ".join(
            f"PITCH_LEDGER_{str(e['loc'][0]).upper()}" for e in exc.errors() if e["loc"]
        )
        return [
            Check(
                "settings",
                False,
                f"invalid value for {fields}: provider must be exactly 'offline' or 'foundry' "
                "(one variable per line)",
            )
        ], None
    checks = [Check("provider", True, settings.model_provider)]
    if settings.model_provider == "offline":
        checks.append(Check("models", True, "offline templates: no Foundry settings needed"))
        return checks, settings
    endpoint = settings.foundry_project_endpoint
    if not endpoint:
        checks.append(Check("FOUNDRY_PROJECT_ENDPOINT", False, "not set"))
    elif not ENDPOINT_PATTERN.match(endpoint):
        checks.append(
            Check(
                "FOUNDRY_PROJECT_ENDPOINT",
                False,
                "expected https://<resource>.services.ai.azure.com/api/projects/<project>",
            )
        )
    else:
        checks.append(Check("FOUNDRY_PROJECT_ENDPOINT", True, endpoint))
    for name, value in (
        ("PITCH_LEDGER_MODEL_STRONG", settings.model_strong),
        ("PITCH_LEDGER_MODEL_SMALL", settings.model_small),
    ):
        checks.append(Check(name, bool(value), value or "not set"))
    missing = [v for v in CREDENTIAL_VARS if not os.environ.get(v)]
    checks.append(
        Check(
            "service principal variables",
            not missing,
            "set (values hidden)" if not missing else f"not set: {', '.join(missing)}",
        )
    )
    return checks, settings


async def check_models(
    clients: dict[str, SupportsChatGetResponse[Any]] | None = None,
    credential: Any = None,
) -> list[Check]:
    """Settings, Entra ID sign-in and one tiny call per deployment (at most 4 calls with
    retries). ``clients`` and ``credential`` replace the real ones in tests."""
    checks, settings = check_settings()
    if settings is None or settings.model_provider == "offline":
        return checks
    if not all(c.ok for c in checks if c.name != "service principal variables"):
        return checks
    own_credential = credential is None
    if own_credential:
        from azure.identity.aio import DefaultAzureCredential

        credential = DefaultAzureCredential()
    try:
        await credential.get_token(TOKEN_SCOPE)
        checks.append(Check("Entra ID sign-in", True, "token issued for Foundry"))
    except Exception as exc:  # wrong tenant, client or secret
        checks.append(Check("Entra ID sign-in", False, _short(exc)))
        return checks
    finally:
        if own_credential:
            await credential.close()
    manager = ModelManager(settings, clients=clients)
    try:
        tiers: dict[str, Role] = {}
        for role, tier in (("explainer", "strong"), ("translator", "small")):
            tiers.setdefault(settings.deployment(tier) or tier, role)  # type: ignore[arg-type]
        for deployment, role in tiers.items():
            start = time.perf_counter()
            try:
                reply = await manager.complete(role, PING_INSTRUCTIONS, PingRequest(), CopyText)
                ok = reply.text.strip().lower() == "ok"
                detail = f"replied in {time.perf_counter() - start:.1f} s" + (
                    "" if ok else f" (unexpected text {reply.text[:40]!r})"
                )
            except ModelUnavailable as exc:
                ok, detail = False, _short(exc.__cause__ or exc)
            checks.append(Check(f"deployment {deployment}", ok, detail))
        usage = manager.usage
        checks.append(
            Check(
                "usage",
                True,
                f"{usage.calls} calls, tokens in {usage.input_tokens}, out {usage.output_tokens}",
            )
        )
    finally:
        await manager.close()
    return checks
