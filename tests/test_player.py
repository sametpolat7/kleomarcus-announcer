import datetime
import threading
import time
import wave
from collections.abc import Callable, Hashable
from pathlib import Path

import pytest

from announcer.chime import CHIME_PATH
from announcer.player import Heard, Playback, PlaybackError, Player, PygameOutput
from support import FakeOutput

CHIME = Path("chime.wav")
AYSE, MEHMET, ZEYNEP = Path("ayse.mp3"), Path("mehmet.mp3"), Path("zeynep.mp3")
AUDIO = {"Ayşe": AYSE, "Mehmet": MEHMET, "Zeynep": ZEYNEP}
AYSE_SLOW = Path("ayse-slow.mp3")
YES, NO_SPEECH, UNPLAYABLE = Heard.YES, Heard.NO_SPEECH, Heard.UNPLAYABLE
LATE, CUT = Heard.LATE, Heard.CUT


def find(text: str, speed: float) -> Path | None:
    if speed == 1.0:
        return AUDIO.get(text)
    return AYSE_SLOW if (text, speed) == ("Ayşe", 0.8) else None


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def outcomes() -> list[Playback]:
    return []


@pytest.fixture
def started() -> list[Hashable | None]:
    return []


@pytest.fixture
def speaking() -> list[tuple[Hashable | None, int]]:
    return []


@pytest.fixture
def player(
    output: FakeOutput,
    outcomes: list[Playback],
    started: list[Hashable | None],
    speaking: list[tuple[Hashable | None, int]],
    clock: FakeClock,
    no_waiting: None,
) -> Player:
    """A player over the fake audio above that reports into ``outcomes``, the
    tags of the groups that begin to sound into ``started``, and the tag and
    place of each text that begins to sound into ``speaking``."""
    return Player(
        find,
        output,
        outcomes.append,
        chime=CHIME,
        clock=clock,
        on_started=started.append,
        on_speaking=lambda tag, index: speaking.append((tag, index)),
    )


def sleep_while_playing(
    output: FakeOutput, clock: FakeClock, file: Path, seconds: float = 3 * 3600
) -> None:
    def while_playing(path: Path) -> None:
        if path == file:
            clock.now += seconds

    output.while_playing = while_playing


def test_chime_sounds_once_before_the_texts_in_order(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.play(["Mehmet", "Ayşe"])

    assert output.played == [CHIME, MEHMET, AYSE]
    assert outcomes == [
        Playback(
            ("Mehmet", "Ayşe"), missing=(), late=(), failed=False, heard=(YES, YES)
        )
    ]
    assert (output.opened, output.closed) == (1, 1)


def test_group_is_spoken_with_the_speech_of_its_speed(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.play(["Ayşe"], speed=0.8)
    player.play(["Ayşe", "Mehmet"], speed=0.8)

    assert output.played == [CHIME, AYSE_SLOW, CHIME, AYSE_SLOW]
    assert outcomes[1].missing == ("Mehmet",)


def test_text_without_speech_is_skipped_and_reported(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.play(["Can", "Ayşe", "Elif"])

    assert output.played == [CHIME, AYSE]
    assert outcomes == [
        Playback(
            ("Can", "Ayşe", "Elif"),
            missing=("Can", "Elif"),
            late=(),
            failed=False,
            heard=(NO_SPEECH, YES, NO_SPEECH),
        )
    ]


def test_nothing_sounds_when_no_text_has_speech(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.play(["Can"])

    assert output.played == []
    assert output.opened == 0
    assert outcomes == [
        Playback(("Can",), missing=("Can",), late=(), failed=False, heard=(NO_SPEECH,))
    ]


def test_missing_device_is_reported(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    output.no_device = True

    player.play(["Ayşe"])

    assert outcomes == [
        Playback(("Ayşe",), missing=(), late=(), failed=True, heard=(UNPLAYABLE,))
    ]


def test_files_after_an_unplayable_one_are_still_played(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    output.unplayable = {MEHMET}

    player.play(["Ayşe", "Mehmet", "Zeynep"])

    assert output.played == [CHIME, AYSE, ZEYNEP]
    assert outcomes == [
        Playback(
            ("Ayşe", "Mehmet", "Zeynep"),
            missing=(),
            late=(),
            failed=True,
            heard=(YES, UNPLAYABLE, YES),
        )
    ]
    assert output.closed == 1


def test_unplayable_file_marks_only_its_own_text_by_its_place_among_the_texts(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    output.unplayable = {MEHMET}

    player.play(["Can", "Mehmet", "Ayşe", "Zeynep"])

    assert output.played == [CHIME, AYSE, ZEYNEP]
    assert outcomes == [
        Playback(
            ("Can", "Mehmet", "Ayşe", "Zeynep"),
            missing=("Can",),
            late=(),
            failed=True,
            heard=(NO_SPEECH, UNPLAYABLE, YES, YES),
        )
    ]


def test_device_that_cannot_be_opened_marks_every_spoken_text_unplayable(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    output.no_device = True

    player.play(["Ayşe", "Can", "Mehmet"])

    assert output.played == []
    assert outcomes == [
        Playback(
            ("Ayşe", "Can", "Mehmet"),
            missing=("Can",),
            late=(),
            failed=True,
            heard=(UNPLAYABLE, NO_SPEECH, UNPLAYABLE),
        )
    ]


def test_unplayable_chime_fails_the_group_but_marks_no_text(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    output.unplayable = {CHIME}

    player.play(["Ayşe", "Mehmet"])

    assert output.played == [AYSE, MEHMET]
    assert outcomes == [
        Playback(("Ayşe", "Mehmet"), missing=(), late=(), failed=True, heard=(YES, YES))
    ]


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
            heard=(YES, LATE, LATE),
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
            heard=(NO_SPEECH, LATE, LATE),
        )
    ]


def test_sleep_in_the_pause_between_two_files_makes_the_rest_late(
    player: Player,
    output: FakeOutput,
    outcomes: list[Playback],
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    busy = output.busy

    def busy_then_asleep() -> bool:
        playing = busy()
        if not playing and output.played[-1] == AYSE:
            clock.now += 3 * 3600
        return playing

    monkeypatch.setattr(output, "busy", busy_then_asleep)

    player.play(["Ayşe", "Mehmet"])

    assert output.played == [CHIME, AYSE]
    assert outcomes[0].late == ("Mehmet",)


def test_sleep_during_the_second_of_two_identical_texts_makes_only_that_one_late(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    def asleep_during_the_second(path: Path) -> None:
        if output.played.count(AYSE) == 2:
            clock.now += 3 * 3600

    output.while_playing = asleep_during_the_second

    player.play(["Ayşe", "Ayşe"])

    assert output.played == [CHIME, AYSE, AYSE]
    assert outcomes == [
        Playback(
            ("Ayşe", "Ayşe"),
            missing=(),
            late=("Ayşe",),
            failed=False,
            heard=(YES, LATE),
        )
    ]


def test_unplayable_file_is_still_reported_after_a_sleep(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    output.unplayable = {AYSE}
    sleep_while_playing(output, clock, ZEYNEP)

    player.play(["Ayşe", "Mehmet", "Zeynep"])

    assert outcomes == [
        Playback(
            ("Ayşe", "Mehmet", "Zeynep"),
            missing=(),
            late=("Zeynep",),
            failed=True,
            heard=(UNPLAYABLE, YES, LATE),
        )
    ]


def test_only_a_gap_over_the_threshold_is_a_sleep(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
    sleep_while_playing(output, clock, AYSE, seconds=Player._SLEEP)
    player.play(["Ayşe", "Mehmet"])
    sleep_while_playing(output, clock, AYSE, seconds=Player._SLEEP + 1)
    player.play(["Ayşe", "Mehmet"])

    assert output.played == [CHIME, AYSE, MEHMET, CHIME, AYSE]
    assert [playback.late for playback in outcomes] == [(), ("Ayşe", "Mehmet")]


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
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)


def run_queue(player: Player, outcomes: list[Playback], expected: int) -> None:
    player.start()
    try:
        wait_for(lambda: len(outcomes) >= expected)
    finally:
        player.stop()


def test_background_thread_plays_the_queue_in_order(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    six_pm = datetime.datetime(2026, 11, 9, 18, 0)
    player.enqueue(["Ayşe"], six_pm)
    player.enqueue(["Mehmet"])

    run_queue(player, outcomes, expected=2)

    assert output.played == [CHIME, AYSE, CHIME, MEHMET]
    assert [playback.texts for playback in outcomes] == [("Ayşe",), ("Mehmet",)]
    assert [playback.tag for playback in outcomes] == [six_pm, None]


def test_queued_group_keeps_its_speed(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.enqueue(["Ayşe"], speed=0.8)
    player.enqueue(["Ayşe"])

    run_queue(player, outcomes, expected=2)

    assert output.played == [CHIME, AYSE_SLOW, CHIME, AYSE]


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
        Playback(("Ayşe",), missing=(), late=("Ayşe",), failed=False, heard=(LATE,)),
        Playback(
            ("Mehmet", "Can"),
            missing=("Can",),
            late=("Mehmet",),
            failed=False,
            heard=(LATE, NO_SPEECH),
        ),
        Playback(
            ("Zeynep",), missing=(), late=("Zeynep",), failed=False, heard=(LATE,)
        ),
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
        Playback(("Ayşe",), missing=(), late=(), failed=False, heard=(YES,)),
        Playback(
            ("Mehmet",), missing=(), late=("Mehmet",), failed=False, heard=(LATE,)
        ),
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
    assert outcomes[1] == Playback(
        ("Mehmet",), missing=(), late=(), failed=False, heard=(YES,)
    )


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
        Playback(("Ayşe",), missing=(), late=("Ayşe",), failed=False, heard=(LATE,)),
        Playback(
            ("Mehmet",), missing=(), late=("Mehmet",), failed=False, heard=(LATE,)
        ),
        Playback(("Zeynep",), missing=(), late=(), failed=False, heard=(YES,)),
    ]


def test_waiting_behind_a_long_announcement_is_not_late(
    player: Player, output: FakeOutput, outcomes: list[Playback], clock: FakeClock
) -> None:
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

    player = Player(find, output, on_played, chime=CHIME, clock=clock)
    player.enqueue(["Ayşe"])
    player.enqueue(["Mehmet"])

    run_queue(player, reported, expected=2)

    assert output.played == [CHIME, AYSE, CHIME, MEHMET]


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
    assert all(thread.name != "player" for thread in threading.enumerate())


@pytest.mark.usefixtures("no_waiting")
def test_start_of_a_group_is_told_with_its_tag_before_it_sounds(
    output: FakeOutput, clock: FakeClock
) -> None:
    events: list[tuple[str, object]] = []
    output.while_playing = lambda path: events.append(("sounds", path))
    player = Player(
        find,
        output,
        lambda playback: events.append(("played", playback.tag)),
        chime=CHIME,
        clock=clock,
        on_started=lambda tag: events.append(("started", tag)),
    )

    player.play(["Ayşe"], "first")
    player.play(["Can"], "without speech")
    player.play(["Mehmet"])

    assert events == [
        ("started", "first"),
        ("sounds", CHIME),
        ("sounds", AYSE),
        ("played", "first"),
        ("played", "without speech"),
        ("started", None),
        ("sounds", CHIME),
        ("sounds", MEHMET),
        ("played", None),
    ]


def test_start_is_not_told_for_a_group_skipped_as_late(
    player: Player,
    output: FakeOutput,
    outcomes: list[Playback],
    started: list[Hashable | None],
    clock: FakeClock,
) -> None:
    def while_playing(path: Path) -> None:
        if path == AYSE:
            clock.now += 3 * 3600
            player.enqueue(["Zeynep"], "after the wake")

    output.while_playing = while_playing
    player.enqueue(["Ayşe"], "cut by the sleep")
    player.enqueue(["Mehmet"], "before the sleep")

    run_queue(player, outcomes, expected=3)

    assert started == ["cut by the sleep", "after the wake"]
    assert [playback.late for playback in outcomes] == [("Ayşe",), ("Mehmet",), ()]


@pytest.mark.usefixtures("no_waiting")
def test_each_text_is_told_with_its_place_once_its_file_has_started(
    output: FakeOutput, clock: FakeClock
) -> None:
    events: list[tuple[object, ...]] = []
    output.while_playing = lambda path: events.append(("sounds", path))
    player = Player(
        find,
        output,
        lambda playback: events.append(("played", playback.tag)),
        chime=CHIME,
        clock=clock,
        on_started=lambda tag: events.append(("started", tag)),
        on_speaking=lambda tag, index: events.append(
            ("speaking", tag, index, output.played[-1])
        ),
    )

    player.play(["Ayşe", "Mehmet"], "first")

    assert events == [
        ("started", "first"),
        ("sounds", CHIME),
        ("speaking", "first", 0, AYSE),
        ("sounds", AYSE),
        ("speaking", "first", 1, MEHMET),
        ("sounds", MEHMET),
        ("played", "first"),
    ]


def test_place_of_a_text_counts_the_texts_without_speech_which_are_never_told(
    player: Player,
    output: FakeOutput,
    speaking: list[tuple[Hashable | None, int]],
) -> None:
    player.play(["Can", "Ayşe", "Elif", "Mehmet"], "first")
    player.play(["Can"], "without speech")

    assert output.played == [CHIME, AYSE, MEHMET]
    assert speaking == [("first", 1), ("first", 3)]


def test_unplayable_file_is_not_told_and_does_not_shift_the_places(
    player: Player,
    output: FakeOutput,
    outcomes: list[Playback],
    speaking: list[tuple[Hashable | None, int]],
) -> None:
    output.unplayable = {CHIME, MEHMET}

    player.play(["Ayşe", "Mehmet", "Zeynep"], "first")

    assert output.played == [AYSE, ZEYNEP]
    assert speaking == [("first", 0), ("first", 2)]
    assert outcomes[0].failed


def test_texts_after_a_cut_are_never_told(
    player: Player,
    output: FakeOutput,
    speaking: list[tuple[Hashable | None, int]],
) -> None:
    def cut_during_mehmet(path: Path) -> None:
        if path == MEHMET:
            player.cut("first")

    output.while_playing = cut_during_mehmet

    player.play(["Ayşe", "Mehmet", "Zeynep"], "first")
    player.play(["Zeynep"], "second")

    assert speaking == [("first", 0), ("first", 1), ("second", 0)]


def test_texts_after_a_sleep_and_groups_skipped_as_late_are_never_told(
    player: Player,
    output: FakeOutput,
    outcomes: list[Playback],
    speaking: list[tuple[Hashable | None, int]],
    clock: FakeClock,
) -> None:
    def while_playing(path: Path) -> None:
        if path == AYSE:
            clock.now += 3 * 3600
            player.enqueue(["Zeynep"], "after the wake")

    output.while_playing = while_playing
    player.enqueue(["Ayşe", "Mehmet"], "cut by the sleep")
    player.enqueue(["Mehmet"], "before the sleep")

    run_queue(player, outcomes, expected=3)

    assert speaking == [("cut by the sleep", 0), ("after the wake", 0)]
    assert [playback.late for playback in outcomes] == [
        ("Ayşe", "Mehmet"),
        ("Mehmet",),
        (),
    ]


def test_cut_ends_the_group_that_sounds_and_the_next_one_still_plays(
    player: Player,
    output: FakeOutput,
    outcomes: list[Playback],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Player, "_POLL", 0.01)

    def cut_during_mehmet(path: Path) -> None:
        if path == MEHMET:
            player.cut("first")

    output.while_playing = cut_during_mehmet
    player.enqueue(["Ayşe", "Mehmet", "Zeynep"], "first")
    player.enqueue(["Zeynep"], "second")

    run_queue(player, outcomes, expected=2)

    assert output.played == [CHIME, AYSE, MEHMET, CHIME, ZEYNEP]
    assert outcomes == [
        Playback(
            ("Ayşe", "Mehmet", "Zeynep"),
            missing=(),
            late=(),
            failed=False,
            tag="first",
            cut=("Mehmet", "Zeynep"),
            heard=(YES, CUT, CUT),
        ),
        Playback(
            ("Zeynep",),
            missing=(),
            late=(),
            failed=False,
            tag="second",
            heard=(YES,),
        ),
    ]
    assert output.closed == 2


def test_cut_during_the_chime_cuts_every_text(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    output.while_playing = lambda path: player.cut("first")

    player.play(["Can", "Ayşe", "Mehmet"], "first")

    assert output.played == [CHIME]
    assert outcomes == [
        Playback(
            ("Can", "Ayşe", "Mehmet"),
            missing=("Can",),
            late=(),
            failed=False,
            tag="first",
            cut=("Ayşe", "Mehmet"),
            heard=(NO_SPEECH, CUT, CUT),
        )
    ]


def test_cut_during_the_second_of_two_identical_texts_cuts_only_that_one(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    def cut_during_the_second(path: Path) -> None:
        if output.played.count(AYSE) == 2:
            player.cut("first")

    output.while_playing = cut_during_the_second

    player.play(["Ayşe", "Ayşe"], "first")

    assert output.played == [CHIME, AYSE, AYSE]
    assert outcomes == [
        Playback(
            ("Ayşe", "Ayşe"),
            missing=(),
            late=(),
            failed=False,
            tag="first",
            cut=("Ayşe",),
            heard=(YES, CUT),
        )
    ]


def test_cut_of_a_group_that_does_not_sound_cuts_nothing(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    def press(path: Path) -> None:
        if path == AYSE:
            player.cut("second")
            player.cut(None)
        elif path == MEHMET:
            player.cut("first")

    output.while_playing = press

    player.play(["Ayşe", "Zeynep"], "first")
    player.play(["Mehmet", "Zeynep"], "second")

    assert output.played == [CHIME, AYSE, ZEYNEP, CHIME, MEHMET, ZEYNEP]
    assert [(playback.cut, playback.late) for playback in outcomes] == [
        ((), ()),
        ((), ()),
    ]


def test_cut_after_the_last_file_ended_reports_nothing_as_cut(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    output.while_closing = lambda: player.cut("first")

    player.play(["Ayşe", "Mehmet"], "first")
    player.play(["Zeynep"], "second")

    assert output.played == [CHIME, AYSE, MEHMET, CHIME, ZEYNEP]
    assert outcomes == [
        Playback(
            ("Ayşe", "Mehmet"),
            missing=(),
            late=(),
            failed=False,
            tag="first",
            heard=(YES, YES),
        ),
        Playback(
            ("Zeynep",),
            missing=(),
            late=(),
            failed=False,
            tag="second",
            heard=(YES,),
        ),
    ]


def test_cut_while_nothing_sounds_spares_the_next_group(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.cut(None)

    player.play(["Ayşe"])

    assert output.played == [CHIME, AYSE]
    assert outcomes[0].cut == ()


def test_silence_cuts_the_group_that_sounds_and_drops_only_those_queued_before_it(
    player: Player,
    output: FakeOutput,
    outcomes: list[Playback],
    started: list[Hashable | None],
) -> None:
    def silence_during_mehmet(path: Path) -> None:
        if path == MEHMET:
            player.silence()
            player.enqueue(["Ayşe"], "after the silence")

    output.while_playing = silence_during_mehmet
    player.enqueue(["Ayşe", "Mehmet", "Zeynep"], "first")
    player.enqueue(["Zeynep"], "second")
    player.enqueue(["Can", "Zeynep"], "third")

    run_queue(player, outcomes, expected=2)

    assert output.played == [CHIME, AYSE, MEHMET, CHIME, AYSE]
    assert started == ["first", "after the silence"]
    assert outcomes == [
        Playback(
            ("Ayşe", "Mehmet", "Zeynep"),
            missing=(),
            late=(),
            failed=False,
            tag="first",
            cut=("Mehmet", "Zeynep"),
            heard=(YES, CUT, CUT),
        ),
        Playback(
            ("Ayşe",),
            missing=(),
            late=(),
            failed=False,
            tag="after the silence",
            heard=(YES,),
        ),
    ]
    assert output.closed == 2


def test_silence_while_nothing_sounds_or_is_queued_spares_the_next_group(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.silence()
    player.enqueue(["Ayşe"], "first")

    run_queue(player, outcomes, expected=1)

    assert output.played == [CHIME, AYSE]
    assert outcomes == [
        Playback(
            ("Ayşe",), missing=(), late=(), failed=False, tag="first", heard=(YES,)
        )
    ]


def test_play_announces_in_full_after_a_silence_cut_off_the_one_before(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    def silence_during_the_chime(path: Path) -> None:
        if output.played == [CHIME]:
            player.silence()

    output.while_playing = silence_during_the_chime

    player.play(["Can", "Ayşe"], "first")
    player.play(["Mehmet", "Zeynep"], "second")

    assert output.played == [CHIME, CHIME, MEHMET, ZEYNEP]
    assert [(playback.tag, playback.heard) for playback in outcomes] == [
        ("first", (NO_SPEECH, CUT)),
        ("second", (YES, YES)),
    ]


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


def test_nothing_is_played_after_stop(
    player: Player, output: FakeOutput, outcomes: list[Playback]
) -> None:
    player.stop()  # harmless for a player that never started

    player.play(["Ayşe"])

    assert output.opened == 0
    assert outcomes == []


def write_silence(path: Path, seconds: float) -> Path:
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(8000)
        file.writeframes(bytes(2 * round(8000 * seconds)))
    return path


def test_pygame_plays_files_to_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcomes: list[Playback]
) -> None:
    # A device that makes no sound, as on a CI runner.
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    write_silence(tmp_path / "speech.wav", 0.2)
    player = Player(
        lambda text, speed: tmp_path / text,
        PygameOutput(),
        outcomes.append,
        chime=write_silence(tmp_path / "chime.wav", 0.2),
    )

    started = time.perf_counter()
    player.play(["speech.wav"])
    elapsed = time.perf_counter() - started

    # Two files of 0.2 seconds and a pause of 0.3 seconds between them.
    assert 0.6 < elapsed < 6
    assert outcomes == [
        Playback(("speech.wav",), missing=(), late=(), failed=False, heard=(YES,))
    ]


def test_pygame_output_plays_the_bundled_chime_and_reports_what_it_cannot_play(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    (tmp_path / "damaged.mp3").write_bytes(b"not audio")
    output = PygameOutput()
    output.open()
    try:
        for unplayable in ("deleted.mp3", "damaged.mp3"):
            with pytest.raises(PlaybackError):
                output.start(tmp_path / unplayable)
            assert not output.busy()
        output.start(CHIME_PATH)
        assert output.busy()
    finally:
        output.close()

    monkeypatch.setenv("SDL_AUDIODRIVER", "no-such-driver")
    with pytest.raises(PlaybackError):
        output.open()
