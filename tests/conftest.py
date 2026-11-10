"""Fixtures shared by the test modules."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from announcer.app import App
from announcer.player import Player
from support import ROWS, FakeClock, FakeOutput, FakeProvider, at, write_schedule


@pytest.fixture
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the real-time pauses out; for the tests that use the fake output."""
    monkeypatch.setattr(Player, "_GAP", 0.0)
    monkeypatch.setattr(Player, "_POLL", 0.0)


@pytest.fixture
def clock() -> FakeClock:
    """A clock that stands at eight in the morning of the test Monday."""
    return FakeClock(at(8))


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def output() -> FakeOutput:
    return FakeOutput()


@pytest.fixture
def settings_file(tmp_path: Path) -> Path:
    return tmp_path / "settings" / "config.json"


@pytest.fixture
def schedule(tmp_path: Path) -> Path:
    """A schedule file of made-up athletes."""
    return write_schedule(tmp_path / "schedule.xlsx", ROWS)


@pytest.fixture
def make_app(
    tmp_path: Path,
    settings_file: Path,
    provider: FakeProvider,
    output: FakeOutput,
    clock: FakeClock,
    no_waiting: None,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[], App]]:
    """Build apps over the fakes above; they are stopped after the test."""
    monkeypatch.setattr("announcer.app._WATCH", 0.02)  # look at the file often
    apps: list[App] = []

    def make() -> App:
        app = App(
            settings_file, tmp_path / "audio", provider, output, clock, interval=0.01
        )
        apps.append(app)
        return app

    yield make
    for app in apps:
        app.stop()


@pytest.fixture
def app(make_app: Callable[[], App]) -> App:
    return make_app()
