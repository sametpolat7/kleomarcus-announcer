"""Fixtures shared by the test modules."""

import pytest

from announcer.player import Player


@pytest.fixture
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the real-time pauses out; for the tests that use the fake output."""
    monkeypatch.setattr(Player, "_GAP", 0.0)
    monkeypatch.setattr(Player, "_POLL", 0.0)
