"""Tests for announcing queued texts and for the pygame audio output."""

import threading
import time
import wave
from collections.abc import Sequence
from pathlib import Path

import pytest

from announcer.chime import CHIME_PATH
from announcer.player import Playback, PlaybackError, Player, PygameOutput

CHIME = Path("chime.wav")
AUDIO = {"Ayşe": Path("ayse.mp3"), "Mehmet": Path("mehmet.mp3")}


class FakeOutput:
    """An output that records what it was asked to play."""

    def __init__(self) -> None:
        self.played: list[list[Path]] = []
        self.interrupted = False
        self.broken = False

    def play(self, paths: Sequence[Path]) -> None:
        if self.broken:
            raise PlaybackError("no sound device")
        self.played.append(list(paths))

    def interrupt(self) -> None:
        self.interrupted = True


def make_player(output: FakeOutput) -> tuple[Player, list[Playback]]:
    """Return a player over the fake audio above and the outcomes it reports."""
    outcomes: list[Playback] = []
    return Player(AUDIO.get, output, outcomes.append, chime=CHIME), outcomes


def test_chime_sounds_once_before_the_texts_in_order() -> None:
    output = FakeOutput()
    player, outcomes = make_player(output)

    player.play(["Mehmet", "Ayşe"])

    assert output.played == [[CHIME, AUDIO["Mehmet"], AUDIO["Ayşe"]]]
    assert outcomes == [Playback(("Mehmet", "Ayşe"), missing=(), failed=False)]


def test_text_without_speech_is_skipped_and_reported() -> None:
    output = FakeOutput()
    player, outcomes = make_player(output)

    player.play(["Zeynep", "Ayşe", "Can"])

    assert output.played == [[CHIME, AUDIO["Ayşe"]]]
    assert outcomes == [
        Playback(("Zeynep", "Ayşe", "Can"), missing=("Zeynep", "Can"), failed=False)
    ]


def test_nothing_sounds_when_no_text_has_speech() -> None:
    output = FakeOutput()
    player, outcomes = make_player(output)

    player.play(["Zeynep"])

    assert output.played == []
    assert outcomes == [Playback(("Zeynep",), missing=("Zeynep",), failed=False)]


def test_output_failure_is_reported() -> None:
    output = FakeOutput()
    output.broken = True
    player, outcomes = make_player(output)

    player.play(["Ayşe"])

    assert outcomes == [Playback(("Ayşe",), missing=(), failed=True)]


def test_the_bundled_chime_is_the_default() -> None:
    output = FakeOutput()
    Player(AUDIO.get, output, lambda playback: None).play(["Ayşe"])

    assert output.played == [[CHIME_PATH, AUDIO["Ayşe"]]]


def test_background_thread_plays_the_queue_in_order() -> None:
    output = FakeOutput()
    outcomes: list[Playback] = []
    done = threading.Event()

    def on_played(playback: Playback) -> None:
        outcomes.append(playback)
        if len(outcomes) == 2:
            done.set()

    player = Player(AUDIO.get, output, on_played, chime=CHIME)
    player.enqueue(["Ayşe"])
    player.enqueue(["Mehmet"])
    player.start()
    try:
        assert done.wait(timeout=5)
    finally:
        player.stop()

    assert output.played == [[CHIME, AUDIO["Ayşe"]], [CHIME, AUDIO["Mehmet"]]]
    assert [playback.texts for playback in outcomes] == [("Ayşe",), ("Mehmet",)]


def test_background_thread_survives_a_failing_callback() -> None:
    output = FakeOutput()
    done = threading.Event()

    def on_played(playback: Playback) -> None:
        if playback.texts == ("Ayşe",):
            raise RuntimeError("the first hand-over fails")
        done.set()

    player = Player(AUDIO.get, output, on_played, chime=CHIME)
    player.start()
    try:
        player.enqueue(["Ayşe"])
        player.enqueue(["Mehmet"])
        assert done.wait(timeout=5)
    finally:
        player.stop()

    assert output.played == [[CHIME, AUDIO["Ayşe"]], [CHIME, AUDIO["Mehmet"]]]


def test_stop_interrupts_the_output_and_ends_the_thread() -> None:
    output = FakeOutput()
    player, _ = make_player(output)
    player.start()

    player.stop()

    assert output.interrupted
    assert all(thread.name != "player" for thread in threading.enumerate())


def test_announcement_cut_off_by_stop_is_not_reported() -> None:
    playing = threading.Event()

    class BlockingOutput(FakeOutput):
        """Plays until it is interrupted."""

        def __init__(self) -> None:
            super().__init__()
            self.released = threading.Event()

        def play(self, paths: Sequence[Path]) -> None:
            playing.set()
            assert self.released.wait(timeout=5)

        def interrupt(self) -> None:
            self.released.set()

    player, outcomes = make_player(BlockingOutput())
    player.start()
    player.enqueue(["Ayşe"])
    assert playing.wait(timeout=5)

    player.stop()

    assert outcomes == []


def test_stop_drops_what_is_still_queued() -> None:
    output = FakeOutput()
    player, outcomes = make_player(output)
    player.enqueue(["Ayşe"])
    player.enqueue(["Mehmet"])

    player.stop()
    player.start()
    player.stop()

    assert output.played == []
    assert outcomes == []


def test_stopping_a_player_that_never_started_is_harmless() -> None:
    output = FakeOutput()
    player, _ = make_player(output)

    player.stop()

    assert output.interrupted


def write_silence(path: Path, seconds: float) -> Path:
    """Write a silent WAV file of the given length."""
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(8000)
        file.writeframes(bytes(2 * round(8000 * seconds)))
    return path


@pytest.fixture
def silent_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make pygame play to a device that makes no sound, as on a CI runner."""
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")


@pytest.mark.usefixtures("silent_device")
def test_pygame_output_plays_files_to_the_end(tmp_path: Path) -> None:
    files = [
        write_silence(tmp_path / "first.wav", 0.2),
        write_silence(tmp_path / "second.wav", 0.2),
    ]

    started = time.perf_counter()
    PygameOutput().play(files)
    elapsed = time.perf_counter() - started

    # Two files of 0.2 seconds and the pause between them.
    assert 0.5 < elapsed < 5


@pytest.mark.usefixtures("silent_device")
def test_pygame_output_plays_the_bundled_chime() -> None:
    PygameOutput().play([CHIME_PATH])


@pytest.mark.usefixtures("silent_device")
def test_pygame_output_can_be_used_again(tmp_path: Path) -> None:
    output = PygameOutput()
    file = write_silence(tmp_path / "short.wav", 0.1)

    output.play([file])
    output.play([file])


@pytest.mark.usefixtures("silent_device")
def test_pygame_output_stops_when_interrupted(tmp_path: Path) -> None:
    output = PygameOutput()
    files = [write_silence(tmp_path / "long.wav", 30)] * 2
    thread = threading.Thread(target=output.play, args=(files,))

    thread.start()
    time.sleep(0.3)
    output.interrupt()
    thread.join(timeout=5)

    assert not thread.is_alive()


@pytest.mark.usefixtures("silent_device")
def test_pygame_output_plays_nothing_after_an_interrupt(tmp_path: Path) -> None:
    output = PygameOutput()
    output.interrupt()

    started = time.perf_counter()
    output.play([write_silence(tmp_path / "long.wav", 30)])

    assert time.perf_counter() - started < 5


@pytest.mark.usefixtures("silent_device")
def test_file_that_is_not_audio_is_a_playback_error(tmp_path: Path) -> None:
    file = tmp_path / "broken.mp3"
    file.write_text("not audio", encoding="utf-8")

    with pytest.raises(PlaybackError):
        PygameOutput().play([file])


@pytest.mark.usefixtures("silent_device")
def test_missing_file_is_a_playback_error(tmp_path: Path) -> None:
    with pytest.raises(PlaybackError):
        PygameOutput().play([tmp_path / "deleted.mp3"])


@pytest.mark.usefixtures("silent_device")
def test_files_after_an_unplayable_one_are_still_played(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"")
    files = [tmp_path / "deleted.mp3", broken, write_silence(tmp_path / "ok.wav", 0.4)]

    started = time.perf_counter()
    with pytest.raises(PlaybackError, match="2 of 3"):
        PygameOutput().play(files)

    assert time.perf_counter() - started > 0.4


@pytest.mark.usefixtures("silent_device")
def test_player_reports_a_file_that_went_missing(tmp_path: Path) -> None:
    outcomes: list[Playback] = []
    player = Player(
        lambda text: tmp_path / "deleted.mp3",
        PygameOutput(),
        outcomes.append,
        chime=write_silence(tmp_path / "chime.wav", 0.1),
    )

    player.play(["Ayşe"])

    assert outcomes == [Playback(("Ayşe",), missing=(), failed=True)]


def test_missing_sound_device_is_a_playback_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDL_AUDIODRIVER", "no-such-driver")

    with pytest.raises(PlaybackError):
        PygameOutput().play([write_silence(tmp_path / "short.wav", 0.1)])
