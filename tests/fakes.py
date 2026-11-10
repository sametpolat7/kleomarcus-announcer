"""Stand-ins for the clock, the speech service and the audio output."""

import datetime
from collections.abc import Callable
from pathlib import Path

from announcer.player import PlaybackError
from announcer.tts import TtsError


class FakeClock:
    """A clock that the test moves by hand."""

    def __init__(self, now: datetime.datetime) -> None:
        self.now = now

    def __call__(self) -> datetime.datetime:
        return self.now


class FakeProvider:
    """A provider that returns predictable audio and records what it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.offline = False
        self.unspeakable: set[str] = set()

    def synthesize(self, text: str, voice: str) -> bytes:
        self.calls.append((text, voice))
        if self.offline or text in self.unspeakable:
            raise TtsError("no speech")
        return f"{voice}|{text}".encode()


class FakeOutput:
    """An output that records what it was asked to play.

    Each file is busy for one look, or for ever if ``endless`` is set.
    ``while_playing`` is called during a look with the file, which is where a
    test lets time pass.
    """

    def __init__(self) -> None:
        self.played: list[Path] = []
        self.opened = 0
        self.closed = 0
        self.no_device = False
        self.unplayable: set[Path] = set()
        self.endless = False
        self.while_playing: Callable[[Path], None] = lambda path: None
        self.while_closing: Callable[[], None] = lambda: None
        self._busy = False

    def open(self) -> None:
        if self.no_device:
            raise PlaybackError("no sound device")
        self.opened += 1

    def start(self, path: Path) -> None:
        if path in self.unplayable:
            raise PlaybackError(f"cannot play {path}")
        self.played.append(path)
        self._busy = True

    def busy(self) -> bool:
        if self._busy:
            self._busy = self.endless
            self.while_playing(self.played[-1])
            return True
        return False

    def close(self) -> None:
        self.closed += 1
        self.while_closing()
