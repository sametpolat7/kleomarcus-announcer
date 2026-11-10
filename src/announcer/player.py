"""Play announcements through the default audio output, one slot at a time.

Texts to announce are queued and played on a background thread: one chime,
then the stored speech of each text. A text without stored speech is skipped
and reported, so the app can show it.

Nothing is announced late. If the computer goes to sleep while texts are
playing or waiting in the queue, they are skipped and reported when it wakes.
"""

import logging
import os
import queue
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from announcer.chime import CHIME_PATH

# pygame prints a greeting when it is imported unless this is set first.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

_log = logging.getLogger(__name__)


class PlaybackError(Exception):
    """The audio output could not play, for example with no sound device."""


class AudioOutput(Protocol):
    """Something that can play audio files out loud."""

    def open(self) -> None:
        """Get ready to play. Raises ``PlaybackError`` if there is no device."""
        ...

    def start(self, path: Path) -> None:
        """Start playing the file and return at once.

        Raises ``PlaybackError`` if the file cannot be played.
        """
        ...

    def busy(self) -> bool:
        """Return whether the file that was started is still playing."""
        ...

    def close(self) -> None:
        """Stop any sound and let go of the device."""
        ...


class PygameOutput:
    """The computer's default audio output, driven by pygame's mixer."""

    def open(self) -> None:
        try:
            pygame.mixer.init()
        except pygame.error as error:
            raise PlaybackError("Cannot open the audio device") from error

    def start(self, path: Path) -> None:
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
        # A missing file is an ``OSError``, a damaged one a pygame error.
        except (pygame.error, OSError) as error:
            raise PlaybackError(f"Cannot play {path}") from error

    def busy(self) -> bool:
        return pygame.mixer.music.get_busy()

    def close(self) -> None:
        pygame.mixer.quit()


@dataclass(frozen=True)
class Playback:
    """What happened when one group of texts was due to be announced."""

    texts: tuple[str, ...]  # everything that was due
    missing: tuple[str, ...]  # the texts skipped for lack of stored speech
    late: tuple[str, ...]  # the texts skipped because the computer was asleep
    failed: bool  # the audio output could not play some or all of the rest


class _AsleepError(Exception):
    """The computer slept while a file was playing or about to play."""

    def __init__(self, file_index: int) -> None:
        super().__init__(f"Slept at file {file_index}")
        self.file_index = file_index


class Player:
    """Announce queued texts on a background thread.

    ``find_audio`` returns the stored speech for a text, or None. ``on_played``
    is called on the player's thread after each group, so it should only hand
    the result over, for example by putting it on a queue. ``clock`` gives the
    time of day in seconds and is how a sleep is noticed. A player runs once:
    after ``stop`` it cannot be started again.
    """

    _GAP = 0.3  # seconds of silence between two files
    _POLL = 0.05  # seconds between two looks at whether a file has ended
    # Two looks are never this many seconds apart while the computer runs, so
    # a longer gap means that it slept in between.
    _SLEEP = 10.0

    def __init__(
        self,
        find_audio: Callable[[str], Path | None],
        output: AudioOutput,
        on_played: Callable[[Playback], None],
        chime: Path = CHIME_PATH,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._find_audio = find_audio
        self._output = output
        self._on_played = on_played
        self._chime = chime
        self._clock = clock
        self._looked_at = 0.0  # when the clock was last looked at
        self._asleep_until = float("-inf")  # texts queued before this are late
        self._queue: queue.Queue[tuple[float, tuple[str, ...]] | None] = queue.Queue()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name="player", daemon=True)

    def enqueue(self, texts: Sequence[str]) -> None:
        """Announce ``texts`` together, after whatever is already queued."""
        self._queue.put((self._clock(), tuple(texts)))

    def start(self) -> None:
        """Start announcing what is queued."""
        self._thread.start()

    def stop(self) -> None:
        """Cut off the current announcement, drop the queue and end the thread.

        Nothing is reported for an announcement that is cut off or dropped.
        """
        self._stopped.set()
        self._queue.put(None)
        if self._thread.is_alive():
            self._thread.join()

    def play(self, texts: Sequence[str]) -> None:
        """Announce ``texts`` now and report the outcome; returns when done.

        The chime sounds once, before the first text. If no text has stored
        speech, nothing is played at all.
        """
        spoken: list[tuple[str, Path]] = []
        missing: list[str] = []
        for text in texts:
            path = self._find_audio(text)
            if path is None:
                missing.append(text)
            else:
                spoken.append((text, path))

        late: tuple[str, ...] = ()
        failed = False
        if spoken:
            try:
                failed = self._play_files([self._chime, *(path for _, path in spoken)])
            except PlaybackError:
                _log.exception("Cannot play an announcement")
                failed = True
            except _AsleepError as woke:
                # File 0 is the chime, so file n belongs to the text n - 1. The
                # text that was cut off counts as late, like those after it.
                first_late = max(woke.file_index - 1, 0)
                late = tuple(text for text, _ in spoken[first_late:])
        if self._stopped.is_set():
            return  # cut off by ``stop``, so there is nothing true to report
        self._on_played(Playback(tuple(texts), tuple(missing), late, failed))

    def _play_files(self, paths: Sequence[Path]) -> bool:
        """Play the files in order; return whether any of them could not be.

        Raises ``PlaybackError`` if there is no audio device, and ``_AsleepError``
        if the computer slept before the last file ended.
        """
        unplayable = False
        # The device is opened for each announcement and closed again, so
        # speakers that were plugged in or switched since are used.
        self._output.open()
        try:
            self._looked_at = self._clock()
            for index, path in enumerate(paths):
                if index and not self._wait(self._GAP, index):
                    return unplayable
                try:
                    self._output.start(path)
                except PlaybackError:
                    # The files after an unplayable one are still played.
                    _log.exception("Cannot play %s", path)
                    unplayable = True
                    continue
                while self._output.busy():
                    if not self._wait(self._POLL, index):
                        return unplayable
        finally:
            self._output.close()
        return unplayable

    def _wait(self, seconds: float, file_index: int) -> bool:
        """Wait a moment; return False if the player was stopped meanwhile.

        Raises ``_AsleepError`` if far more time has passed than was waited for.
        """
        stopped = self._stopped.wait(seconds)
        before, now = self._looked_at, self._clock()
        self._looked_at = now
        if now - before > self._SLEEP:
            # The sleep fills all but a moment of the gap, so whatever was
            # queued in its first half was queued before the computer slept.
            self._asleep_until = before + (now - before) / 2
            raise _AsleepError(file_index)
        return not stopped

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None or self._stopped.is_set():
                break
            queued_at, texts = item
            try:
                if queued_at < self._asleep_until:
                    self._on_played(
                        Playback(texts, missing=(), late=texts, failed=False)
                    )
                else:
                    self.play(texts)
            except Exception:
                # One failed announcement must not silence every later one.
                _log.exception("Announcing failed")
