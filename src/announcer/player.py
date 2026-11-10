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
    failed: bool  # the audio output could not play some or all of the others


class Player:
    """Announce queued texts on a background thread.

    ``find_audio`` returns the stored speech for a text, or None. ``on_played``
    is called on the player's thread after each group, so it should only hand
    the result over, for example by putting it on a queue. ``clock`` gives the
    time of day in seconds and is how a sleep is noticed. A player runs once:
    after ``stop`` it cannot be started again.
    """

    _GAP = 0.3  # seconds of silence between two files
    _POLL = 0.1  # seconds between two looks at whether a file has ended
    # Two looks are never this many seconds apart while the computer runs, even
    # when it is very busy, so a longer gap means that it slept in between.
    _SLEEP = 30.0

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
        self._looked_at = self._clock()
        self._announce(tuple(texts), skip=False)

    def _announce(self, texts: tuple[str, ...], skip: bool) -> None:
        """Announce ``texts`` and report the outcome.

        With ``skip`` nothing is played, because the texts are already late.
        """
        if self._stopped.is_set():
            return
        spoken: list[tuple[str, Path]] = []
        missing: list[str] = []
        for text in texts:
            path = self._find_audio(text)
            if path is None:
                missing.append(text)
            else:
                spoken.append((text, path))

        failed = False
        finished = 0  # how many files, the chime included, were played out
        if spoken and not skip:
            files = [self._chime, *(path for _, path in spoken)]
            try:
                failed, finished = self._play_files(files)
            except PlaybackError:
                _log.exception("Cannot play an announcement")
                failed, finished = True, len(files)
        if self._stopped.is_set():
            return  # cut off by ``stop``, so there is nothing true to report
        # File 0 is the chime, so file n belongs to text n - 1. A text that
        # was cut off by a sleep counts as late, like those after it.
        late = tuple(text for text, _ in spoken[max(finished - 1, 0) :])
        self._on_played(Playback(texts, tuple(missing), late, failed))

    def _play_files(self, paths: Sequence[Path]) -> tuple[bool, int]:
        """Play the files in order until the last has ended.

        Returns whether any file could not be played, and how many files were
        dealt with before the player was stopped or the computer slept: all of
        them if neither happened. Raises ``PlaybackError`` if there is no
        audio device.
        """
        unplayable = False
        # The device is opened for each announcement and closed again, so
        # speakers that were plugged in or switched since are used.
        self._output.open()
        try:
            for index, path in enumerate(paths):
                if index and not self._wait(self._GAP):
                    return unplayable, index
                try:
                    self._output.start(path)
                except PlaybackError:
                    # The files after an unplayable one are still played.
                    _log.exception("Cannot play %s", path)
                    unplayable = True
                    continue
                while self._output.busy():
                    if not self._wait(self._POLL):
                        return unplayable, index
        finally:
            self._output.close()
        return unplayable, len(paths)

    def _wait(self, seconds: float) -> bool:
        """Wait a moment; return False if playing has to end here.

        That is the case when the player was stopped or the computer slept.
        """
        stopped = self._stopped.wait(seconds)
        return not (self._slept() or stopped)

    def _slept(self) -> bool:
        """Look at the clock; return whether the computer slept since the last look."""
        before, now = self._looked_at, self._clock()
        self._looked_at = now
        if now - before <= self._SLEEP:
            return False
        # The sleep fills all but a moment of the gap, so whatever was queued
        # in its first half was queued before the computer slept.
        self._asleep_until = before + (now - before) / 2
        return True

    def _run(self) -> None:
        self._looked_at = self._clock()
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                # Nothing was waiting, so nothing can be late: start afresh.
                self._asleep_until = float("-inf")
                item = self._queue.get()
                self._looked_at = self._clock()
            if item is None or self._stopped.is_set():
                break
            queued_at, texts = item
            try:
                self._slept()  # a sleep between two announcements counts too
                self._announce(texts, skip=queued_at < self._asleep_until)
            except Exception:
                # One failed announcement must not silence every later one.
                _log.exception("Announcing failed")
