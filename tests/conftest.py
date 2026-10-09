"""Tests never reach a paid model, whatever the local environment or ``.env`` says."""

import pytest


@pytest.fixture(autouse=True)
def _offline_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITCH_LEDGER_MODEL_PROVIDER", "offline")
