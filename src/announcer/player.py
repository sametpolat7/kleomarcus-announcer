"""Play announcements through the default audio output, one slot at a time.

Texts to announce are queued and played on a background thread: one chime,
then the stored speech of each text. A text without stored speech is skipped
and reported, so the app can show it.
"""

import logging
import os
import queue
import threading
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

    def play(self, paths: Sequence[Path]) -> None:
        """Play the files one after another and return when the last has ended.

        Raises ``PlaybackError`` if the device or any of the files cannot be
        played; the files that can are played first.
        """
        ...

    def interrupt(self) -> None:
        """Cut off playback now and refuse any later one."""
        ...


class PygameOutput:
    """The computer's default audio output, driven by pygame's mixer."""

    _GAP = 0.3  # seconds of silence between two files
    _POLL = 0.05  # seconds between two looks at whether a file has ended

    def __init__(self) -> None:
        self._interrupted = threading.Event()

    def play(self, paths: Sequence[Path]) -> None:
        try:
            # The output device is opened for each playback and closed again,
            # so speakers that were plugged in or switched since are used.
            pygame.mixer.init()
        except pygame.error as error:
            raise PlaybackError("Cannot open the audio device") from error

        unplayable = 0
        try:
            for index, path in enumerate(paths):
                if self._interrupted.wait(self._GAP if index else 0):
                    break
                try:
                    pygame.mixer.music.load(path)
                    pygame.mixer.music.play()
                # A missing file is an ``OSError``, a damaged one a pygame error.
                # Either way the files after it are still played.
                except pygame.error, OSError:
                    _log.exception("Cannot play %s", path)
                    unplayable += 1
                    continue
                while pygame.mixer.music.get_busy():
                    if self._interrupted.wait(self._POLL):
                        break
        finally:
            pygame.mixer.quit()
        if unplayable:
            raise PlaybackError(f"{unplayable} of {len(paths)} files cannot be played")

    def interrupt(self) -> None:
        self._interrupted.set()


@dataclass(frozen=True)
class Playback:
    """What happened when one group of texts was due to be announced."""

    texts: tuple[str, ...]  # everything that was due
    missing: tuple[str, ...]  # the texts skipped for lack of stored speech
    failed: bool  # the audio output could not play


class Player:
    """Announce queued texts on a background thread.

    ``find_audio`` returns the stored speech for a text, or None. ``on_played``
    is called on the player's thread after each group, so it should only hand
    the result over, for example by putting it on a queue. A player runs once:
    after ``stop`` it cannot be started again.
    """

    def __init__(
        self,
        find_audio: Callable[[str], Path | None],
        output: AudioOutput,
        on_played: Callable[[Playback], None],
        chime: Path = CHIME_PATH,
    ) -> None:
        self._find_audio = find_audio
        self._output = output
        self._on_played = on_played
        self._chime = chime
        self._queue: queue.Queue[tuple[str, ...] | None] = queue.Queue()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name="player", daemon=True)

    def enqueue(self, texts: Sequence[str]) -> None:
        """Announce ``texts`` together, after whatever is already queued."""
        self._queue.put(tuple(texts))

    def start(self) -> None:
        """Start announcing what is queued."""
        self._thread.start()

    def stop(self) -> None:
        """Cut off the current announcement, drop the queue and end the thread.

        Nothing is reported for an announcement that is cut off or dropped.
        """
        self._stopped.set()
        self._output.interrupt()
        self._queue.put(None)
        if self._thread.is_alive():
            self._thread.join()

    def play(self, texts: Sequence[str]) -> None:
        """Announce ``texts`` now and report the outcome; returns when done.

        The chime sounds once, before the first text. If no text has stored
        speech, nothing is played at all.
        """
        paths: list[Path] = []
        missing: list[str] = []
        for text in texts:
            path = self._find_audio(text)
            if path is None:
                missing.append(text)
            else:
                paths.append(path)

        failed = False
        if paths:
            try:
                self._output.play([self._chime, *paths])
            except PlaybackError:
                _log.exception("Playing an announcement failed")
                failed = True
        if self._stopped.is_set():
            return  # cut off by ``stop``, so there is nothing true to report
        self._on_played(Playback(tuple(texts), tuple(missing), failed))

    def _run(self) -> None:
        while True:
            texts = self._queue.get()
            if texts is None or self._stopped.is_set():
                break
            try:
                self.play(texts)
            except Exception:
                # One failed announcement must not silence every later one.
                _log.exception("Announcing failed")
