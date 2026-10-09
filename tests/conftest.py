"""Tests never reach a paid model or a cloud feed or store, whatever the environment says."""

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True, scope="session")
def _offline_models() -> Iterator[None]:
    """Session-wide, so module- and session-scoped fixtures are covered too."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PITCH_LEDGER_MODEL_PROVIDER", "offline")
        patch.setenv("PITCH_LEDGER_FEED", "file")
        patch.setenv("PITCH_LEDGER_STORE", "file")
        patch.setenv("PITCH_LEDGER_LEDGER", "file")
        patch.setenv("PITCH_LEDGER_QUEUE", "memory")
        patch.setenv("PITCH_LEDGER_PUSH", "off")
        yield
