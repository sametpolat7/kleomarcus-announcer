"""Tests for the speech cache and the Edge provider, without a network."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from announcer import APP_NAME
from announcer.tts import (
    AudioCache,
    EdgeTtsProvider,
    TtsError,
    audio_cache_dir,
)

VOICE = "tr-TR-EmelNeural"
HELLO = "Ayşe Yılmaz, hoş geldiniz."
BYE = "Salon kapanıyor."


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


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def cache(tmp_path: Path, provider: FakeProvider) -> AudioCache:
    return AudioCache(tmp_path / "audio", provider, VOICE)


def test_audio_cache_dir_is_a_folder_of_the_app() -> None:
    directory = audio_cache_dir()

    assert directory.name == "audio"
    assert APP_NAME in directory.parts
    assert directory.is_absolute()


def test_nothing_is_stored_at_first(cache: AudioCache) -> None:
    assert cache.find(HELLO) is None


def test_ensure_generates_and_stores_the_speech(
    cache: AudioCache, provider: FakeProvider
) -> None:
    path = cache.ensure(HELLO)

    assert path.read_bytes() == f"{VOICE}|{HELLO}".encode()
    assert path.suffix == ".mp3"
    assert cache.find(HELLO) == path
    assert provider.calls == [(HELLO, VOICE)]


def test_stored_speech_is_reused(cache: AudioCache, provider: FakeProvider) -> None:
    first = cache.ensure(HELLO)
    provider.offline = True

    assert cache.ensure(HELLO) == first
    assert len(provider.calls) == 1


def test_stored_speech_survives_a_restart(
    tmp_path: Path, cache: AudioCache, provider: FakeProvider
) -> None:
    path = cache.ensure(HELLO)

    reopened = AudioCache(tmp_path / "audio", provider, VOICE)

    assert reopened.find(HELLO) == path
    assert len(provider.calls) == 1


def test_each_text_has_its_own_file(cache: AudioCache) -> None:
    assert cache.ensure(HELLO) != cache.ensure(BYE)
    assert cache.find(HELLO.upper()) is None


def test_each_voice_has_its_own_file(
    tmp_path: Path, cache: AudioCache, provider: FakeProvider
) -> None:
    other = AudioCache(tmp_path / "audio", provider, "tr-TR-AhmetNeural")
    cache.ensure(HELLO)

    assert other.find(HELLO) is None
    assert other.ensure(HELLO).read_bytes() == f"tr-TR-AhmetNeural|{HELLO}".encode()
    assert other.ensure(HELLO) != cache.ensure(HELLO)


def test_failed_generation_raises_and_stores_nothing(
    tmp_path: Path, cache: AudioCache, provider: FakeProvider
) -> None:
    provider.offline = True

    with pytest.raises(TtsError):
        cache.ensure(HELLO)

    assert cache.find(HELLO) is None
    assert not (tmp_path / "audio").exists()


def test_speech_is_generated_once_the_network_is_back(
    cache: AudioCache, provider: FakeProvider
) -> None:
    provider.offline = True
    with pytest.raises(TtsError):
        cache.ensure(HELLO)
    provider.offline = False

    assert cache.ensure(HELLO).is_file()


def test_storage_failure_is_a_tts_error(tmp_path: Path, provider: FakeProvider) -> None:
    blocker = tmp_path / "audio"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    cache = AudioCache(blocker, provider, VOICE)

    with pytest.raises(TtsError):
        cache.ensure(HELLO)


def test_only_finished_files_are_left_in_the_folder(
    tmp_path: Path, cache: AudioCache
) -> None:
    cache.ensure(HELLO)
    cache.ensure(BYE)

    assert {path.suffix for path in (tmp_path / "audio").iterdir()} == {".mp3"}
    assert len(list((tmp_path / "audio").iterdir())) == 2


def test_ensure_all_generates_only_what_is_missing(
    cache: AudioCache, provider: FakeProvider
) -> None:
    cache.ensure(HELLO)

    assert cache.ensure_all([HELLO, BYE, BYE, HELLO]) == ()

    assert provider.calls == [(HELLO, VOICE), (BYE, VOICE)]
    assert cache.find(BYE) is not None


def test_ensure_all_returns_the_texts_without_speech(
    cache: AudioCache, provider: FakeProvider
) -> None:
    provider.unspeakable = {BYE, "…"}

    assert cache.ensure_all([BYE, HELLO, "…", BYE]) == (BYE, "…")
    assert cache.find(HELLO) is not None


def test_ensure_all_offline_keeps_what_is_stored(
    cache: AudioCache, provider: FakeProvider
) -> None:
    cache.ensure(HELLO)
    provider.offline = True

    assert cache.ensure_all([HELLO, BYE]) == (BYE,)
    assert cache.find(HELLO) is not None


def test_ensure_all_of_nothing(cache: AudioCache, provider: FakeProvider) -> None:
    assert cache.ensure_all([]) == ()
    assert provider.calls == []


class FakeCommunicate:
    """Stands in for ``edge_tts.Communicate``; set ``chunks`` or ``error``."""

    chunks: tuple[dict[str, object], ...] = ()
    error: Exception | None = None
    requests: list[tuple[str, str]]

    def __init__(self, text: str, voice: str) -> None:
        self.requests.append((text, voice))

    async def stream(self) -> AsyncIterator[dict[str, object]]:
        for chunk in self.chunks:
            yield chunk
        if self.error is not None:
            raise self.error


@pytest.fixture
def communicate(monkeypatch: pytest.MonkeyPatch) -> type[FakeCommunicate]:
    class Communicate(FakeCommunicate):
        requests: list[tuple[str, str]] = []  # noqa: RUF012

    monkeypatch.setattr("announcer.tts.edge_tts.Communicate", Communicate)
    return Communicate


def test_edge_provider_joins_the_audio_it_receives(
    communicate: type[FakeCommunicate],
) -> None:
    communicate.chunks = (
        {"type": "audio", "data": b"ID3"},
        {"type": "SentenceBoundary", "text": HELLO},
        {"type": "audio", "data": b"speech"},
    )

    assert EdgeTtsProvider().synthesize(HELLO, VOICE) == b"ID3speech"
    assert communicate.requests == [(HELLO, VOICE)]


def test_edge_provider_without_audio_is_an_error(
    communicate: type[FakeCommunicate],
) -> None:
    communicate.chunks = ({"type": "SentenceBoundary", "text": HELLO},)

    with pytest.raises(TtsError):
        EdgeTtsProvider().synthesize(HELLO, VOICE)


@pytest.mark.parametrize(
    "error",
    [OSError("no network"), TimeoutError(), ValueError("bad voice"), RuntimeError()],
)
def test_edge_provider_failure_is_a_tts_error(
    communicate: type[FakeCommunicate], error: Exception
) -> None:
    communicate.chunks = ({"type": "audio", "data": b"partial"},)
    communicate.error = error

    with pytest.raises(TtsError) as raised:
        EdgeTtsProvider().synthesize(HELLO, VOICE)

    assert raised.value.__cause__ is error
