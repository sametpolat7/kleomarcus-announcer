import hashlib
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from announcer.tts import AudioCache, EdgeTtsProvider, TtsError
from support import FakeProvider

VOICE = "tr-TR-EmelNeural"
HELLO = "Ayşe Yılmaz, hoş geldiniz."
BYE = "Salon kapanıyor."


@pytest.fixture
def cache(tmp_path: Path, provider: FakeProvider) -> AudioCache:
    return AudioCache(tmp_path / "audio", provider, VOICE)


def test_ensure_generates_and_stores_the_speech(
    cache: AudioCache, provider: FakeProvider
) -> None:
    assert cache.find(HELLO) is None

    path = cache.ensure(HELLO)

    assert path.read_bytes() == f"{VOICE}|{HELLO}".encode()
    assert path.suffix == ".mp3"
    assert cache.find(HELLO) == path
    assert provider.calls == [(HELLO, VOICE)]


def test_stored_speech_is_reused_without_a_network_and_after_a_restart(
    tmp_path: Path, cache: AudioCache, provider: FakeProvider
) -> None:
    first = cache.ensure(HELLO)
    provider.offline = True

    reopened = AudioCache(tmp_path / "audio", provider, VOICE)

    assert cache.ensure(HELLO) == reopened.ensure(HELLO) == first
    assert len(provider.calls) == 1


def test_each_text_and_each_voice_has_its_own_file(
    tmp_path: Path, cache: AudioCache, provider: FakeProvider
) -> None:
    other = AudioCache(tmp_path / "audio", provider, "tr-TR-AhmetNeural")
    hello = cache.ensure(HELLO)

    assert cache.ensure(BYE) != hello
    assert other.find(HELLO) is None
    assert other.ensure(HELLO) != hello
    assert other.ensure(HELLO).read_bytes() == f"tr-TR-AhmetNeural|{HELLO}".encode()


def test_each_speed_has_its_own_file(cache: AudioCache, provider: FakeProvider) -> None:
    normal = cache.ensure(HELLO)
    assert cache.find(HELLO, 0.9) is None

    slow = cache.ensure(HELLO, 0.9)

    assert slow != normal
    assert (cache.find(HELLO), cache.find(HELLO, 0.9)) == (normal, slow)
    assert cache.find(HELLO, 1.1) is None
    assert provider.speeds == [1.0, 0.9]


def test_speech_at_the_normal_speed_is_named_as_before_there_were_speeds(
    cache: AudioCache,
) -> None:
    digest = hashlib.sha256(f"{VOICE}\n{HELLO}".encode()).hexdigest()

    assert cache.ensure(HELLO).name == f"{digest}.mp3"


def test_failed_generation_stores_nothing_and_is_made_up_for_later(
    tmp_path: Path, cache: AudioCache, provider: FakeProvider
) -> None:
    provider.offline = True

    with pytest.raises(TtsError):
        cache.ensure(HELLO)

    assert cache.find(HELLO) is None
    assert not (tmp_path / "audio").exists()

    provider.offline = False
    assert cache.ensure(HELLO).is_file()


def test_storage_failure_is_a_tts_error(tmp_path: Path, provider: FakeProvider) -> None:
    blocker = tmp_path / "audio"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    cache = AudioCache(blocker, provider, VOICE)

    with pytest.raises(TtsError):
        cache.ensure(HELLO)


def test_ensure_all_generates_only_what_is_missing(
    cache: AudioCache, provider: FakeProvider
) -> None:
    cache.ensure(HELLO)

    assert cache.ensure_all([HELLO, BYE, BYE, HELLO]) == ()

    assert provider.calls == [(HELLO, VOICE), (BYE, VOICE)]
    assert cache.find(BYE) is not None


def test_ensure_all_generates_what_is_missing_at_the_speed(
    cache: AudioCache, provider: FakeProvider
) -> None:
    cache.ensure(HELLO)
    cache.ensure(BYE, 1.2)

    assert cache.ensure_all([HELLO, BYE], 1.2) == ()

    assert provider.speeds == [1.0, 1.2, 1.2]
    assert cache.find(HELLO, 1.2) is not None
    assert cache.find(BYE) is None


def test_ensure_all_returns_the_texts_without_speech_in_the_order_given(
    cache: AudioCache, provider: FakeProvider
) -> None:
    provider.unspeakable = {BYE, "…"}

    assert cache.ensure_all([BYE, HELLO, "…", BYE]) == (BYE, "…")
    assert cache.find(HELLO) is not None


def test_ensure_all_generates_several_sentences_at_a_time(tmp_path: Path) -> None:
    together = threading.Barrier(3, timeout=5)

    class WaitingProvider:
        def synthesize(self, text: str, voice: str, speed: float) -> bytes:
            together.wait()
            return text.encode()

    cache = AudioCache(tmp_path / "audio", WaitingProvider(), VOICE)
    texts = [f"Sporcu {number}" for number in range(6)]

    assert cache.ensure_all(texts) == ()
    assert all(cache.find(text) is not None for text in texts)


def test_clear_removes_every_stored_speech_so_that_it_is_generated_again(
    tmp_path: Path, cache: AudioCache, provider: FakeProvider
) -> None:
    ahmet = "tr-TR-AhmetNeural"
    other = AudioCache(tmp_path / "audio", provider, ahmet)
    cache.ensure(HELLO)
    cache.ensure(BYE, 0.9)
    other.ensure(HELLO)

    cache.clear()

    assert list((tmp_path / "audio").iterdir()) == []
    assert (cache.find(HELLO), cache.find(BYE, 0.9), other.find(HELLO)) == (
        None,
        None,
        None,
    )
    assert cache.ensure(HELLO).read_bytes() == f"{VOICE}|{HELLO}".encode()
    assert provider.calls == [
        (HELLO, VOICE),
        (BYE, VOICE),
        (HELLO, ahmet),
        (HELLO, VOICE),
    ]


def test_clear_does_nothing_when_no_speech_was_ever_stored(
    tmp_path: Path, cache: AudioCache
) -> None:
    cache.clear()

    assert not (tmp_path / "audio").exists()
    assert cache.ensure(HELLO).is_file()


def test_closed_cache_stores_neither_what_is_being_generated_nor_what_is_asked_later(
    tmp_path: Path,
    cache: AudioCache,
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = cache.ensure("Hoş geldiniz.")
    synthesize = provider.synthesize

    def closed_meanwhile(text: str, voice: str, speed: float) -> bytes:
        cache.close()
        return synthesize(text, voice, speed)

    monkeypatch.setattr(provider, "synthesize", closed_meanwhile)
    monkeypatch.setattr(AudioCache, "_WORKERS", 1)

    assert cache.ensure_all([HELLO, BYE]) == (HELLO, BYE)

    assert list((tmp_path / "audio").iterdir()) == [stored]
    assert provider.calls == [("Hoş geldiniz.", VOICE), (HELLO, VOICE)]
    assert cache.find("Hoş geldiniz.") == stored
    with pytest.raises(TtsError):
        cache.ensure(BYE)


class FakeCommunicate:
    """Set ``chunks`` or ``error``."""

    chunks: tuple[dict[str, object], ...] = ()
    error: Exception | None = None
    requests: list[tuple[str, str, str]]

    def __init__(self, text: str, voice: str, rate: str) -> None:
        self.requests.append((text, voice, rate))

    async def stream(self) -> AsyncIterator[dict[str, object]]:
        for chunk in self.chunks:
            yield chunk
        if self.error is not None:
            raise self.error


@pytest.fixture
def communicate(monkeypatch: pytest.MonkeyPatch) -> type[FakeCommunicate]:
    class Communicate(FakeCommunicate):
        requests: list[tuple[str, str, str]] = []  # noqa: RUF012

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

    assert EdgeTtsProvider().synthesize(HELLO, VOICE, 1.0) == b"ID3speech"
    assert communicate.requests == [(HELLO, VOICE, "+0%")]


@pytest.mark.parametrize(
    ("speed", "rate"),
    [(0.8, "-20%"), (0.9, "-10%"), (1.0, "+0%"), (1.1, "+10%"), (1.2, "+20%")],
)
def test_edge_provider_asks_for_the_speed_in_percent(
    communicate: type[FakeCommunicate], speed: float, rate: str
) -> None:
    communicate.chunks = ({"type": "audio", "data": b"speech"},)

    EdgeTtsProvider().synthesize(HELLO, VOICE, speed)

    assert communicate.requests == [(HELLO, VOICE, rate)]


def test_edge_provider_without_audio_is_an_error(
    communicate: type[FakeCommunicate],
) -> None:
    communicate.chunks = ({"type": "SentenceBoundary", "text": HELLO},)

    with pytest.raises(TtsError):
        EdgeTtsProvider().synthesize(HELLO, VOICE, 1.0)


@pytest.mark.parametrize("error", [OSError("no network"), RuntimeError("protocol")])
def test_edge_provider_failure_is_a_tts_error(
    communicate: type[FakeCommunicate], error: Exception
) -> None:
    communicate.chunks = ({"type": "audio", "data": b"partial"},)
    communicate.error = error

    with pytest.raises(TtsError) as raised:
        EdgeTtsProvider().synthesize(HELLO, VOICE, 1.0)

    assert raised.value.__cause__ is error
