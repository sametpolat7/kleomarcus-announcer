"""Speech comes from a provider behind a small interface, so the service can be
swapped. Every sentence is generated once and stored as an MP3 file; after
that the app plays it without a network connection.
"""

import asyncio
import hashlib
import logging
import queue
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

import edge_tts
from platformdirs import user_cache_dir

from announcer import APP_NAME
from announcer.files import write_atomically

_log = logging.getLogger(__name__)


class TtsError(Exception):
    """Speech could not be generated or stored."""


class TtsProvider(Protocol):
    def synthesize(self, text: str, voice: str) -> bytes:
        """Return ``text`` spoken by ``voice`` as MP3 data.

        Raises ``TtsError`` if the speech cannot be generated.
        """
        ...


class EdgeTtsProvider:
    def synthesize(self, text: str, voice: str) -> bytes:
        try:
            audio = asyncio.run(_collect(text, voice))
        # The service can fail in many ways: no network, a timeout, an unknown
        # voice, a changed protocol. All of them mean "no speech for now".
        except Exception as error:
            raise TtsError(f"Cannot generate speech with {voice}") from error
        if not audio:
            raise TtsError(f"{voice} returned no audio")
        return audio


async def _collect(text: str, voice: str) -> bytes:
    stream = edge_tts.Communicate(text, voice).stream()
    return b"".join(
        [chunk["data"] async for chunk in stream if chunk["type"] == "audio"]
    )


def audio_cache_dir() -> Path:
    return Path(user_cache_dir(APP_NAME, appauthor=False)) / "audio"


class AudioCache:
    """A speech file is named after a hash of the voice and the text, so a changed
    template or voice leads to new files and never to stale speech. Safe to
    use from several threads.
    """

    _WORKERS = 4  # sentences generated at the same time

    def __init__(self, directory: Path, provider: TtsProvider, voice: str) -> None:
        self._directory = directory
        self._provider = provider
        self._voice = voice

    def find(self, text: str) -> Path | None:
        path = self._path(text)
        return path if path.is_file() else None

    def ensure(self, text: str) -> Path:
        """Raises ``TtsError`` if the speech cannot be generated or stored."""
        path = self._path(text)
        if not path.is_file():
            audio = self._provider.synthesize(text, self._voice)
            try:
                write_atomically(path, audio)
            except OSError as error:
                raise TtsError(f"Cannot store speech in {self._directory}") from error
        return path

    def ensure_all(self, texts: Iterable[str]) -> tuple[str, ...]:
        """Returns the texts that still have no speech, for example because there
        is no network connection. Several sentences are generated at a time,
        since each one takes a few seconds.
        """
        wanted = tuple(dict.fromkeys(texts))
        todo: queue.SimpleQueue[str] = queue.SimpleQueue()
        for text in wanted:
            if self.find(text) is None:
                todo.put(text)
        failed: dict[str, TtsError] = {}

        def work() -> None:
            while True:
                try:
                    text = todo.get_nowait()
                except queue.Empty:
                    return
                try:
                    self.ensure(text)
                except TtsError as error:
                    failed[text] = error

        # Daemon threads, so a long generation never keeps the app from closing.
        workers = [
            threading.Thread(target=work, name="speech", daemon=True)
            for _ in range(min(self._WORKERS, todo.qsize()))
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        unspoken = tuple(text for text in wanted if text in failed)
        if unspoken:
            # One line for all: without a network every sentence fails alike.
            error = failed[unspoken[0]]
            _log.warning(
                'No speech for %d sentence(s), the first of them "%s": %s (%r)',
                len(unspoken),
                unspoken[0],
                error,
                error.__cause__,
            )
        return unspoken

    def _path(self, text: str) -> Path:
        digest = hashlib.sha256(f"{self._voice}\n{text}".encode()).hexdigest()
        return self._directory / f"{digest}.mp3"
