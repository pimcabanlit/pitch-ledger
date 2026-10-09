"""Application settings, read from environment variables (prefix ``PITCH_LEDGER_``) or ``.env``."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_scenarios_dir() -> Path:
    """The repo's ``scenarios/`` folder when running from a checkout, else ``./scenarios``."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists() and (parent / "scenarios").is_dir():
            return parent / "scenarios"
    return Path("scenarios")


class Settings(BaseSettings):
    """Local-first settings. Cloud adapters are added in later phases."""

    model_config = SettingsConfigDict(env_prefix="PITCH_LEDGER_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    scenarios_dir: Path = Field(default_factory=_default_scenarios_dir)
    log_level: str = "INFO"


def get_settings() -> Settings:
    """Return settings resolved from the current environment."""
    return Settings()
