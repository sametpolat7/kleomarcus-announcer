"""Nothing is announced late after a sleep. Only a sleep of under half a minute
goes unnoticed, and what was playing then carries on by that much.
"""

import logging
import os
import queue
import threading
import time
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from announcer.chime import CHIME_PATH
from announcer.models import NORMAL_SPEED

# pygame prints a greeting when it is imported unless this is set first.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

_log = logging.getLogger(__name__)

# When a group of texts was queued, the texts, what the caller tagged it with,
# and the speed of the speech to play.
type _Queued = tuple[float, tuple[str, ...], Hashable | None, float]

_NOTHING = object()  # what is being played while nothing is


class PlaybackError(Exception):
    """The audio output could not play, for example with no sound device."""


class AudioOutput(Protocol):
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


class Heard(Enum):
    """What became of one text of a group."""

    YES = "yes"
    NO_SPEECH = "no speech"  # there was no stored speech for it
    UNPLAYABLE = "unplayable"  # the audio output could not play it
    LATE = "late"  # the computer was asleep
    CUT = "cut"  # cut off by ``Player.cut``


@dataclass(frozen=True)
class Playback:
    texts: tuple[str, ...]  # everything that was due
    missing: tuple[str, ...]  # the texts skipped for lack of stored speech
    late: tuple[str, ...]  # the texts skipped because the computer was asleep
    failed: bool  # the audio output could not play some or all of the others
    tag: Hashable | None = None  # as given when the texts were queued
    cut: tuple[str, ...] = ()  # the texts not heard in full because of ``cut``
    # What became of each text, in the same order, so that two texts that
    # read the same are told apart.
    heard: tuple[Heard, ...] = ()


class Player:
    """``on_played`` is called on the player's thread after each group,
    ``on_started`` with its tag when a group begins to sound, and
    ``on_speaking`` with its tag and the place of a text among its texts when
    that text begins to sound, so they should only hand that over, for
    example by putting it on a queue. A group may be
    queued with a tag; the player only hands that back, so the caller can tell
    which group it was.
    ``find_audio`` is asked for the speech of a text at the speed its group
    was queued with. ``clock`` gives the time of day in seconds and is how a
    sleep is noticed. A player runs once: after ``stop`` it cannot be started
    again.
    """

    _GAP = 0.3  # seconds of silence between two files
    _POLL = 0.1  # seconds between two looks at whether a file has ended
    # Two looks are never this many seconds apart while the computer runs, even
    # when it is very busy, so a longer gap means that it slept in between.
    _SLEEP = 30.0

    def __init__(
        self,
        find_audio: Callable[[str, float], Path | None],
        output: AudioOutput,
        on_played: Callable[[Playback], None],
        chime: Path = CHIME_PATH,
        clock: Callable[[], float] = time.time,
        on_started: Callable[[Hashable | None], None] = lambda tag: None,
        on_speaking: Callable[[Hashable | None, int], None] = lambda tag, index: None,
    ) -> None:
        self._find_audio = find_audio
        self._output = output
        self._on_played = on_played
        self._on_started = on_started
        self._on_speaking = on_speaking
        self._chime = chime
        self._clock = clock
        self._looked_at = 0.0
        self._asleep_until = float("-inf")  # texts queued before this are late
        self._queue: queue.Queue[_Queued | None] = queue.Queue()
        self._stopped = threading.Event()
        self._cut = threading.Event()  # the group that is sounding is to end
        self._sounding: object = _NOTHING  # the tag of that group
        self._sounding_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="player", daemon=True)

    def enqueue(
        self,
        texts: Sequence[str],
        tag: Hashable | None = None,
        speed: float = NORMAL_SPEED,
    ) -> None:
        self._queue.put((self._clock(), tuple(texts), tag, speed))

    def cut(self, tag: Hashable | None) -> None:
        """Safe to call from any thread. Cut off the group with ``tag`` if it is
        being played, within a fraction of a second; the groups queued after it
        are played as usual. Nothing happens once that group is over, so a
        late call never cuts off the one after it.
        """
        with self._sounding_lock:
            if self._sounding is not _NOTHING and self._sounding == tag:
                self._cut.set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Nothing is reported for an announcement that is cut off or dropped."""
        self._stopped.set()
        self._queue.put(None)
        if self._thread.is_alive():
            self._thread.join()

    def play(
        self,
        texts: Sequence[str],
        tag: Hashable | None = None,
        speed: float = NORMAL_SPEED,
    ) -> None:
        """Returns when ``texts`` are announced and the outcome is reported."""
        self._looked_at = self._clock()
        self._announce(tuple(texts), tag, speed, skip=False)

    def _announce(
        self,
        texts: tuple[str, ...],
        tag: Hashable | None,
        speed: float,
        skip: bool,
    ) -> None:
        """With ``skip`` nothing is played, because the texts are already late."""
        if self._stopped.is_set():
            return
        spoken: list[tuple[int, str, Path]] = []  # with its place among ``texts``
        heard = [Heard.YES] * len(texts)
        for index, text in enumerate(texts):
            path = self._find_audio(text, speed)
            if path is None:
                heard[index] = Heard.NO_SPEECH
            else:
                spoken.append((index, text, path))

        def started(file: int) -> None:
            if file:  # file 0 is the chime
                self._on_speaking(tag, spoken[file - 1][0])

        unplayable: set[int] = set()  # files that the output could not play
        finished = 0  # how many files, the chime included, were played out
        cut_off = False
        if spoken and not skip:
            files = [self._chime, *(path for _, _, path in spoken)]
            with self._sounding_lock:
                self._sounding = tag
                self._cut.clear()
            self._on_started(tag)
            try:
                unplayable, finished = self._play_files(files, started)
            except PlaybackError:
                _log.exception("Cannot play an announcement")
                unplayable, finished = set(range(len(files))), len(files)
            finally:
                with self._sounding_lock:
                    self._sounding = _NOTHING
                    cut_off = self._cut.is_set() and finished < len(files)
        if self._stopped.is_set():
            return  # cut off by ``stop``, so there is nothing true to report
        for file in unplayable - {0}:  # file 0 is the chime
            heard[spoken[file - 1][0]] = Heard.UNPLAYABLE
        # File n belongs to text n - 1. A text that was cut off by a sleep
        # counts as late, like those after it; one that was cut off by
        # ``cut`` counts as cut, like those after it.
        for place, _, _ in spoken[max(finished - 1, 0) :]:
            heard[place] = Heard.CUT if cut_off else Heard.LATE
        self._on_played(
            Playback(
                texts,
                _of(texts, heard, Heard.NO_SPEECH),
                _of(texts, heard, Heard.LATE),
                bool(unplayable),
                tag,
                _of(texts, heard, Heard.CUT),
                tuple(heard),
            )
        )

    def _play_files(
        self, paths: Sequence[Path], started: Callable[[int], None]
    ) -> tuple[set[int], int]:
        """Returns the places of the files that could not be played, and how
        many files were dealt with before the player was stopped or cut off,
        or the computer slept: all of them if none of that happened.
        ``started`` is told the place of each file that begins to sound.
        Raises ``PlaybackError`` if there is no audio device.
        """
        unplayable: set[int] = set()
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
                    unplayable.add(index)
                    continue
                started(index)
                while self._output.busy():
                    if not self._wait(self._POLL):
                        return unplayable, index
        finally:
            self._output.close()
        return unplayable, len(paths)

    def _wait(self, seconds: float) -> bool:
        stopped = self._stopped.wait(seconds)
        return not (self._slept() or stopped or self._cut.is_set())

    def _slept(self) -> bool:
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
            queued_at, texts, tag, speed = item
            try:
                self._slept()  # a sleep between two announcements counts too
                skip = queued_at < self._asleep_until
                self._announce(texts, tag, speed, skip)
            except Exception:
                # One failed announcement must not silence every later one.
                _log.exception("Announcing failed")


def _of(texts: Sequence[str], heard: Sequence[Heard], what: Heard) -> tuple[str, ...]:
    return tuple(text for text, was in zip(texts, heard, strict=True) if was is what)
