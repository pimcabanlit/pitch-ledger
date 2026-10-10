"""API settings (``PITCH_LEDGER_API_*``)."""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PITCH_LEDGER_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    api_auth: Literal["entra", "dev"] = "entra"
    """``entra``: producer endpoints need a Microsoft Entra ID token with the producer app
    role (fails closed: refused when the API's Entra settings are missing). ``dev``: a fixed
    local producer, for running on your own machine only."""
    api_audience: str | None = None  # the API app registration's Application ID URI or ID
    api_tenant_id: str | None = Field(
        default=None, validation_alias=AliasChoices("PITCH_LEDGER_API_TENANT_ID", "AZURE_TENANT_ID")
    )
    api_producer_role: str = "Producer"
    api_producers: Literal["role", "any_account"] = "role"
    """Who may act as a producer in ``entra`` mode: ``role`` (production) is our tenant's
    users with the producer app role; ``any_account`` (the public demo) is anyone signed in
    with a Microsoft account, work or personal, recorded by object ID."""
    api_cors_origins: list[str] = ["http://localhost:3000"]
    api_decisions_per_minute: int = 10  # each approval runs model calls (cost)
    api_decision_provider: Literal["offline", "foundry"] | None = None  # default: model settings
