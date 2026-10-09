"""Application settings, read from environment variables (prefix ``PITCH_LEDGER_``) or ``.env``."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Local-first settings. Cloud adapters are added in later phases."""

    model_config = SettingsConfigDict(env_prefix="PITCH_LEDGER_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    log_level: str = "INFO"


def get_settings() -> Settings:
    """Return settings resolved from the current environment."""
    return Settings()
