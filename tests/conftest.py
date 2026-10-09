"""Tests never reach a paid model, whatever the local environment or ``.env`` says."""

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True, scope="session")
def _offline_models() -> Iterator[None]:
    """Session-wide, so module- and session-scoped fixtures are covered too."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PITCH_LEDGER_MODEL_PROVIDER", "offline")
        yield
