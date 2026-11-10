"""Tests for keeping the computer awake."""

import os
import sys

import pytest

from announcer.keep_awake import KeepAwake


def test_start_and_stop_work_on_this_platform() -> None:
    awake = KeepAwake()

    awake.start()
    awake.stop()


def test_starting_and_stopping_twice_is_harmless() -> None:
    awake = KeepAwake()

    awake.start()
    awake.start()
    awake.stop()
    awake.stop()


def test_stopping_without_starting_is_harmless() -> None:
    KeepAwake().stop()


class FakeProcess:
    """Stands in for the caffeinate process."""

    started: list[list[str]] = []  # noqa: RUF012

    def __init__(self, command: list[str]) -> None:
        self.started.append(command)
        self.events: list[str] = []

    def terminate(self) -> None:
        self.events.append("terminate")

    def wait(self) -> int:
        self.events.append("wait")
        return 0


@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch) -> type[FakeProcess]:
    class Process(FakeProcess):
        started: list[list[str]] = []  # noqa: RUF012

    monkeypatch.setattr("announcer.keep_awake.subprocess.Popen", Process)
    return Process


macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


@macos_only
def test_macos_runs_caffeinate_for_as_long_as_this_process(
    popen: type[FakeProcess],
) -> None:
    awake = KeepAwake()

    awake.start()
    awake.start()

    assert popen.started == [["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())]]


@macos_only
def test_macos_ends_caffeinate_on_stop(
    popen: type[FakeProcess], monkeypatch: pytest.MonkeyPatch
) -> None:
    processes: list[FakeProcess] = []
    original = popen.__init__

    def remember(self: FakeProcess, command: list[str]) -> None:
        original(self, command)
        processes.append(self)

    monkeypatch.setattr(popen, "__init__", remember)
    awake = KeepAwake()

    awake.start()
    awake.stop()
    awake.stop()

    assert [process.events for process in processes] == [["terminate", "wait"]]


@macos_only
def test_macos_without_caffeinate_carries_on(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(command: list[str]) -> None:
        raise FileNotFoundError(command[0])

    monkeypatch.setattr("announcer.keep_awake.subprocess.Popen", missing)
    awake = KeepAwake()

    awake.start()
    awake.stop()
