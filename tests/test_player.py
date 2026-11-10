"""Tests for announcing queued texts and for the pygame audio output."""

import datetime
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

import pytest

from announcer.chime import CHIME_PATH
from announcer.player import Playback, PlaybackError, Player, PygameOutput
from fakes import FakeOutput

CHIME = Path("chime.wav")
AYSE, MEHMET, ZEYNEP = Path("ayse.mp3"), Path("mehmet.mp3"), Path("zeynep.mp3")
AUDIO = {"Ayşe": AYSE, "Mehmet": MEHMET, "Zeynep": ZEYNEP}


class FakeClock:
    """A clock that the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def output() -> FakeOutput:
    return FakeOutput()


@pytest.fixture
def outcomes() -> list[Playback]:
    return []


@pytest.fixture
def player(
    output: FakeOutput, outcomes: list[Playback], clock: FakeClock, no_waiting: None
) -> Player:
    """A player over the fake audio above that reports into ``outcomes``."""
    return Player(AUDIO.get, output, outcomes.append, chime=CHIME, clock=clock)


def sleep_while_playing(
    output: FakeOutput, clock: FakeClock, file: Path, seconds: float = 3 * 3600
) -> None:
    """Make the computer sleep for ``seconds`` while ``file`` is playing."""

    def while_playing(path: Path) -> None:
        if path == file:
            clock.now += seconds

    output.while_playing = while_playing


def test_chime_sounds_once_before_the_texts_in_order(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.play(["Mehmet", "Ayşe"])

    assert output.played == [CHIME, MEHMET, AYSE]
    assert outcomes == [Playback(("Mehmet", "Ayşe"), missing=(), late=(), failed=False)]


def test_device_is_opened_and_closed_for_each_announcement(
    player: Player, output: FakeOutput
) -> None:
    player.play(["Ayşe"])
    player.play(["Mehmet"])

    assert (output.opened, output.closed) == (2, 2)


def test_text_without_speech_is_skipped_and_reported(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.play(["Can", "Ayşe", "Elif"])

    assert output.played == [CHIME, AYSE]
    assert outcomes == [
        Playback(
            ("Can", "Ayşe", "Elif"), missing=("Can", "Elif"), late=(), failed=False
        )
    ]


def test_nothing_sounds_when_no_text_has_speech(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.play(["Can"])

    assert output.played == []
    assert output.opened == 0
    assert outcomes == [Playback(("Can",), missing=("Can",), late=(), failed=False)]


def test_missing_device_is_reported(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    output.no_device = True

    player.play(["Ayşe"])

    assert outcomes == [Playback(("Ayşe",), missing=(), late=(), failed=True)]


def test_files_after_an_unplayable_one_are_still_played(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    output.unplayable = {MEHMET}

    player.play(["Ayşe", "Mehmet", "Zeynep"])

    assert output.played == [CHIME, AYSE, ZEYNEP]
    assert outcomes == [
        Playback(("Ayşe", "Mehmet", "Zeynep"), missing=(), late=(), failed=True)
    ]
    assert output.closed == 1


@pytest.mark.usefixtures("no_waiting")
def test_the_bundled_chime_is_the_default(output: FakeOutput) -> None:
    Player(AUDIO.get, output, lambda playback: None).play(["Ayşe"])

    assert output.played == [CHIME_PATH, AYSE]


def test_texts_after_a_sleep_are_skipped_as_late(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    sleep_while_playing(output, clock, MEHMET)

    player.play(["Ayşe", "Mehmet", "Zeynep"])

    # Mehmet was cut off by the sleep, so he counts as late as well.
    assert output.played == [CHIME, AYSE, MEHMET]
    assert outcomes == [
        Playback(
            ("Ayşe", "Mehmet", "Zeynep"),
            missing=(),
            late=("Mehmet", "Zeynep"),
            failed=False,
        )
    ]
    assert output.closed == 1


def test_sleep_during_the_chime_makes_every_text_late(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    sleep_while_playing(output, clock, CHIME)

    player.play(["Can", "Ayşe", "Mehmet"])

    assert output.played == [CHIME]
    assert outcomes == [
        Playback(
            ("Can", "Ayşe", "Mehmet"),
            missing=("Can",),
            late=("Ayşe", "Mehmet"),
            failed=False,
        )
    ]


def test_sleep_during_the_last_text_makes_only_it_late(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    sleep_while_playing(output, clock, MEHMET)

    player.play(["Ayşe", "Mehmet"])

    assert outcomes[0].late == ("Mehmet",)


def test_unplayable_file_is_still_reported_after_a_sleep(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    output.unplayable = {AYSE}
    sleep_while_playing(output, clock, ZEYNEP)

    player.play(["Ayşe", "Mehmet", "Zeynep"])

    assert outcomes == [
        Playback(
            ("Ayşe", "Mehmet", "Zeynep"), missing=(), late=("Zeynep",), failed=True
        )
    ]


@pytest.mark.parametrize("seconds", [Player._SLEEP - 1, Player._SLEEP])
def test_a_gap_up_to_the_threshold_is_not_a_sleep(
    player: Player,
    output: FakeOutput,
    outcomes: list[Playback],
    clock: FakeClock,
    seconds: float,
) -> None:
    sleep_while_playing(output, clock, AYSE, seconds=seconds)

    player.play(["Ayşe", "Mehmet"])

    assert output.played == [CHIME, AYSE, MEHMET]
    assert outcomes[0].late == ()


def test_a_gap_over_the_threshold_is_a_sleep(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    sleep_while_playing(output, clock, AYSE, seconds=Player._SLEEP + 1)

    player.play(["Ayşe", "Mehmet"])

    assert output.played == [CHIME, AYSE]
    assert outcomes[0].late == ("Ayşe", "Mehmet")


def test_clock_set_back_is_not_a_sleep(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    sleep_while_playing(output, clock, AYSE, seconds=-3600)

    player.play(["Ayşe", "Mehmet"])

    assert output.played == [CHIME, AYSE, MEHMET]
    assert outcomes[0].late == ()


def test_time_between_two_announcements_is_not_a_sleep(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    player.play(["Ayşe"])
    clock.now += 3600
    player.play(["Mehmet"])

    assert output.played == [CHIME, AYSE, CHIME, MEHMET]
    assert [playback.late for playback in outcomes] == [(), ()]


def wait_for(condition: Callable[[], bool]) -> None:
    """Wait until ``condition`` holds, for five seconds at most."""
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)


def run_queue(player: Player, outcomes: list[Playback], expected: int) -> None:
    """Run the player's thread until it has reported ``expected`` outcomes."""
    player.start()
    try:
        wait_for(lambda: len(outcomes) >= expected)
    finally:
        player.stop()


def test_background_thread_plays_the_queue_in_order(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.enqueue(["Ayşe"])
    player.enqueue(["Mehmet"])

    run_queue(player, outcomes, expected=2)

    assert output.played == [CHIME, AYSE, CHIME, MEHMET]
    assert [playback.texts for playback in outcomes] == [("Ayşe",), ("Mehmet",)]


def test_outcome_carries_the_due_time_the_texts_were_queued_with(
    player: Player, outcomes: list[Playback]
) -> None:
    six_pm = datetime.datetime(2026, 11, 9, 18, 0)
    player.enqueue(["Ayşe"], six_pm)
    player.enqueue(["Mehmet"])

    run_queue(player, outcomes, expected=2)

    assert [playback.due for playback in outcomes] == [six_pm, None]


def test_texts_queued_before_a_sleep_are_skipped_as_late(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    sleep_while_playing(output, clock, AYSE)
    player.enqueue(["Ayşe"])
    clock.now += 60
    player.enqueue(["Mehmet", "Can"])
    player.enqueue(["Zeynep"])

    run_queue(player, outcomes, expected=3)

    assert output.played == [CHIME, AYSE]
    assert outcomes == [
        Playback(("Ayşe",), missing=(), late=("Ayşe",), failed=False),
        Playback(("Mehmet", "Can"), missing=("Can",), late=("Mehmet",), failed=False),
        Playback(("Zeynep",), missing=(), late=("Zeynep",), failed=False),
    ]


def test_sleep_between_two_announcements_makes_the_next_one_late(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    def while_closing() -> None:
        if output.closed == 1:
            clock.now += 3 * 3600

    output.while_closing = while_closing
    player.enqueue(["Ayşe"])
    player.enqueue(["Mehmet"])

    run_queue(player, outcomes, expected=2)

    assert output.played == [CHIME, AYSE]
    assert outcomes == [
        Playback(("Ayşe",), missing=(), late=(), failed=False),
        Playback(("Mehmet",), missing=(), late=("Mehmet",), failed=False),
    ]


def test_clock_set_back_after_a_sleep_does_not_skip_new_texts(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    sleep_while_playing(output, clock, AYSE, seconds=8 * 3600)
    player.enqueue(["Ayşe"])
    player.start()
    try:
        wait_for(lambda: len(outcomes) == 1)
        time.sleep(0.05)  # let the player find its queue empty
        clock.now -= 5 * 3600
        player.enqueue(["Mehmet"])
        wait_for(lambda: len(outcomes) == 2)
    finally:
        player.stop()

    assert output.played == [CHIME, AYSE, CHIME, MEHMET]
    assert outcomes[1] == Playback(("Mehmet",), missing=(), late=(), failed=False)


def test_texts_queued_after_the_wake_are_played(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    def while_playing(path: Path) -> None:
        if path == AYSE:
            clock.now += 3 * 3600
            # The scheduler fires a slot right after the wake, before the
            # player has noticed the sleep.
            player.enqueue(["Zeynep"])

    output.while_playing = while_playing
    player.enqueue(["Ayşe"])
    player.enqueue(["Mehmet"])

    run_queue(player, outcomes, expected=3)

    assert output.played == [CHIME, AYSE, CHIME, ZEYNEP]
    assert outcomes == [
        Playback(("Ayşe",), missing=(), late=("Ayşe",), failed=False),
        Playback(("Mehmet",), missing=(), late=("Mehmet",), failed=False),
        Playback(("Zeynep",), missing=(), late=(), failed=False),
    ]


def test_waiting_behind_a_long_announcement_is_not_late(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    # Every file takes eight seconds, with no sleep anywhere.
    def while_playing(path: Path) -> None:
        clock.now += 8

    output.while_playing = while_playing
    player.enqueue(["Ayşe", "Mehmet", "Zeynep"])
    player.enqueue(["Mehmet"])

    run_queue(player, outcomes, expected=2)

    assert output.played == [CHIME, AYSE, MEHMET, ZEYNEP, CHIME, MEHMET]
    assert [playback.late for playback in outcomes] == [(), ()]


@pytest.mark.usefixtures("no_waiting")
def test_background_thread_survives_a_failing_callback(
    output: FakeOutput, clock: FakeClock
) -> None:
    reported: list[Playback] = []

    def on_played(playback: Playback) -> None:
        reported.append(playback)
        if playback.texts == ("Ayşe",):
            raise RuntimeError("the first hand-over fails")

    player = Player(AUDIO.get, output, on_played, chime=CHIME, clock=clock)
    player.enqueue(["Ayşe"])
    player.enqueue(["Mehmet"])

    run_queue(player, reported, expected=2)

    assert output.played == [CHIME, AYSE, CHIME, MEHMET]


def test_stop_ends_the_thread(player: Player) -> None:
    player.start()

    player.stop()

    assert all(thread.name != "player" for thread in threading.enumerate())


def test_announcement_cut_off_by_stop_is_not_reported(
    player: Player,
    output: FakeOutput,
    outcomes: list[Playback],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Player, "_POLL", 0.01)
    playing = threading.Event()
    output.endless = True
    output.while_playing = lambda path: playing.set()
    player.enqueue(["Ayşe", "Mehmet"])
    player.enqueue(["Zeynep"])
    player.start()
    assert playing.wait(timeout=5)

    player.stop()

    assert output.played == [CHIME]
    assert output.closed == 1
    assert outcomes == []


def test_stop_drops_what_is_still_queued(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.enqueue(["Ayşe"])
    player.enqueue(["Mehmet"])

    player.stop()
    player.start()
    player.stop()

    assert output.played == []
    assert outcomes == []


def test_stopping_a_player_that_never_started_is_harmless(player: Player) -> None:
    player.stop()


def test_nothing_is_played_after_stop(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.stop()

    player.play(["Ayşe"])

    assert output.opened == 0
    assert outcomes == []


def write_silence(path: Path, seconds: float) -> Path:
    """Write a silent WAV file of the given length."""
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(8000)
        file.writeframes(bytes(2 * round(8000 * seconds)))
    return path


@pytest.fixture
def real_player(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcomes: list[Playback]
) -> Player:
    """A player on pygame with a device that makes no sound, as on a CI runner.

    The speech of a text is the file of that name in ``tmp_path``.
    """
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    return Player(
        lambda text: tmp_path / text,
        PygameOutput(),
        outcomes.append,
        chime=write_silence(tmp_path / "chime.wav", 0.2),
    )


def test_pygame_plays_files_to_the_end(
    tmp_path: Path, real_player: Player, outcomes: list[Playback]
) -> None:
    write_silence(tmp_path / "first.wav", 0.2)
    write_silence(tmp_path / "second.wav", 0.2)

    started = time.perf_counter()
    real_player.play(["first.wav", "second.wav"])
    elapsed = time.perf_counter() - started

    # Three files of 0.2 seconds and two pauses of 0.3 seconds between them.
    assert 0.9 < elapsed < 6
    assert outcomes == [
        Playback(("first.wav", "second.wav"), missing=(), late=(), failed=False)
    ]


def test_pygame_plays_the_bundled_chime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcomes: list[Playback]
) -> None:
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    write_silence(tmp_path / "short.wav", 0.1)
    player = Player(lambda text: tmp_path / text, PygameOutput(), outcomes.append)

    player.play(["short.wav"])

    assert outcomes == [Playback(("short.wav",), missing=(), late=(), failed=False)]


def test_pygame_reports_a_missing_and_a_damaged_file_and_plays_the_rest(
    tmp_path: Path, real_player: Player, outcomes: list[Playback]
) -> None:
    (tmp_path / "damaged.mp3").write_bytes(b"not audio")
    write_silence(tmp_path / "good.wav", 0.4)

    started = time.perf_counter()
    real_player.play(["deleted.mp3", "damaged.mp3", "good.wav"])

    assert time.perf_counter() - started > 0.6
    assert outcomes == [
        Playback(
            ("deleted.mp3", "damaged.mp3", "good.wav"),
            missing=(),
            late=(),
            failed=True,
        )
    ]


def test_pygame_reports_a_missing_sound_device(
    tmp_path: Path,
    real_player: Player,
    outcomes: list[Playback],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SDL_AUDIODRIVER", "no-such-driver")
    write_silence(tmp_path / "short.wav", 0.1)

    real_player.play(["short.wav"])

    assert outcomes == [Playback(("short.wav",), missing=(), late=(), failed=True)]


def test_pygame_stops_in_the_middle_of_a_file(
    tmp_path: Path, real_player: Player, outcomes: list[Playback]
) -> None:
    write_silence(tmp_path / "long.wav", 30)
    real_player.enqueue(["long.wav", "long.wav"])
    real_player.start()
    time.sleep(0.5)

    started = time.perf_counter()
    real_player.stop()

    assert time.perf_counter() - started < 5
    assert outcomes == []


def test_pygame_output_reports_playback_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    output = PygameOutput()
    output.open()
    try:
        with pytest.raises(PlaybackError):
            output.start(tmp_path / "deleted.mp3")
        assert not output.busy()
    finally:
        output.close()

    monkeypatch.setenv("SDL_AUDIODRIVER", "no-such-driver")
    with pytest.raises(PlaybackError):
        output.open()
