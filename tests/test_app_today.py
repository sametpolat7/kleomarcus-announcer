import datetime
import queue
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from announcer.app import (
    TEST_NAME,
    App,
    ChangeError,
    ChangeProblem,
    Editing,
    LastAnnouncement,
    backup_copy,
)
from announcer.changes import load_changes, save_changes
from announcer.chime import CHIME_PATH
from announcer.models import Change, CustomAnnouncement, Kind
from announcer.settings import Settings, load_settings, save_settings
from announcer.template import DEFAULT_END_TEMPLATE, DEFAULT_TEMPLATE, render
from announcer.today import Row, Status
from announcer.ui.texts import status as shown
from support import (
    MONDAY,
    FakeClock,
    FakeOutput,
    FakeProvider,
    at,
    statuses,
    wait_for,
    wait_for_speech,
    write_schedule,
)

SIX_PM, SEVEN_PM, EIGHT_PM = (datetime.time(hour) for hour in (18, 19, 20))
DAY = datetime.timedelta(days=1)
TUESDAY = MONDAY + DAY
# The athletes of ``ROWS``, in its order.
AYSE, MEHMET, ZEYNEP = "Ayşe Yılmaz", "Mehmet Demir", "Zeynep Kaya"
NEW_START, NEW_END = "Hoş geldiniz, {ad_soyad}.", "Güle güle, {ad_soyad}."
WATER = CustomAnnouncement("Su içmeyi unutmayın.", SEVEN_PM)
CLOSING = "Salon kapanıyor."  # a new text for a custom announcement
CHIME = "chime"  # what ``heard`` shows for the chime


def let_time_pass(app: App) -> None:
    """Give the threads a good many looks, for what must not happen."""
    time.sleep(0.1)
    app.poll()


def name_of(row: Row) -> str:
    """The athlete whom the row is for; empty for any other row."""
    return row.key[2] if isinstance(row.key, tuple) else ""


def row_of(app: App, kind: Kind, name: str = "") -> Row:
    return next(
        row
        for row in app.rows()
        if row.kind is kind and (not name or name_of(row) == name)
    )


def status_of(app: App, kind: Kind, name: str = "") -> Status:
    return row_of(app, kind, name).status


def instant(app: App, text: str) -> Row:
    return next(
        row for row in app.rows() if row.kind is Kind.INSTANT and row.text == text
    )


def start_text(name: str) -> str:
    return render(DEFAULT_TEMPLATE, name)


def end_text(name: str) -> str:
    return render(DEFAULT_END_TEMPLATE, name)


def said(path: Path) -> str:
    """The fake provider's speech holds its text."""
    if path == CHIME_PATH:
        return CHIME
    return path.read_text(encoding="utf-8").split("|", 1)[1]


def heard(output: FakeOutput) -> list[str]:
    return [said(path) for path in output.played]


def speech_file(directory: Path, text: str) -> Path:
    return next(path for path in directory.glob("*.mp3") if said(path) == text)


def speak_ahead(app: App, texts: Iterable[str]) -> None:
    """Make the speech of a new text, as the window does before an edit."""
    request = app.make_speech(texts)
    wait_for(app, lambda: request.done)
    assert request.spoken


def skip_everyone(app: App, names: Iterable[str] = (AYSE, MEHMET)) -> None:
    """Skip the start of each athlete's session, and so its end."""
    for name in names:
        app.skip(row_of(app, Kind.START, name))


def held_back(
    provider: FakeProvider, monkeypatch: pytest.MonkeyPatch, text: str
) -> threading.Event:
    release = threading.Event()
    synthesize = provider.synthesize

    def held(spoken: str, voice: str, speed: float) -> bytes:
        if spoken == text:
            release.wait(5)
        return synthesize(spoken, voice, speed)

    monkeypatch.setattr(provider, "synthesize", held)
    return release


def held_sounding(output: FakeOutput, text: str, nth: int = 1) -> threading.Event:
    release = threading.Event()
    times = 0

    def playing(path: Path) -> None:
        nonlocal times
        if said(path) == text:
            times += 1
            if times == nth:
                release.wait(5)

    output.while_playing = playing
    return release


def statuses_while_sounding(
    app: App, output: FakeOutput, files: int
) -> list[tuple[str, list[Status]]]:
    sounding: queue.Queue[Path] = queue.Queue()
    go_on: queue.Queue[None] = queue.Queue()

    def hold(path: Path) -> None:
        sounding.put(path)
        go_on.get(timeout=5)

    output.while_playing = hold
    seen: list[tuple[str, list[Status]]] = []
    while len(seen) < files:
        path = sounding.get(timeout=5)
        # The player told the app about the file before it began to hold it.
        app.poll()
        seen.append((said(path), statuses(app)))
        if len(seen) == files:
            output.while_playing = lambda path: None
        go_on.put(None)
    return seen


def test_end_of_a_session_is_announced_an_hour_after_its_start_with_its_template(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.set_delay(1)
    wait_for_speech(app)

    assert [(row.time, row.kind, row.text) for row in app.rows()] == [
        (datetime.time(18, 1), Kind.START, start_text(AYSE)),
        (datetime.time(18, 1), Kind.START, start_text(MEHMET)),
        (datetime.time(19, 1), Kind.END, end_text(AYSE)),
        (datetime.time(19, 1), Kind.END, end_text(MEHMET)),
    ]
    app.start()
    clock.now = at(18, 1)
    wait_for(app, lambda: statuses(app) == [Status.PLAYED] * 2 + [Status.NEXT] * 2)
    clock.now = at(19)
    let_time_pass(app)
    assert len(output.played) == 3
    clock.now = at(19, 1)
    wait_for(app, lambda: statuses(app) == [Status.PLAYED] * 4)

    assert heard(output) == [
        CHIME,
        start_text(AYSE),
        start_text(MEHMET),
        CHIME,
        end_text(AYSE),
        end_text(MEHMET),
    ]
    assert app.last_announcement == LastAnnouncement(
        at(19, 1), ((Kind.END, AYSE), (Kind.END, MEHMET)), Status.PLAYED
    )


def test_end_and_start_in_the_same_minute_follow_one_chime_the_end_first(
    app: App, output: FakeOutput, clock: FakeClock, tmp_path: Path
) -> None:
    evening = [
        (AYSE, "Pazartesi", "18:00"),
        (AYSE, "Pazartesi", "19:00"),  # back to back
        (MEHMET, "Pazartesi", "19:00"),
    ]
    app.choose_schedule(write_schedule(tmp_path / "evening.xlsx", evening))
    wait_for_speech(app)

    assert [(row.time, row.kind, row.text) for row in app.rows()] == [
        (SIX_PM, Kind.START, start_text(AYSE)),
        (SEVEN_PM, Kind.END, end_text(AYSE)),
        (SEVEN_PM, Kind.START, start_text(AYSE)),
        (SEVEN_PM, Kind.START, start_text(MEHMET)),
        (EIGHT_PM, Kind.END, end_text(AYSE)),
        (EIGHT_PM, Kind.END, end_text(MEHMET)),
    ]
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[0] is Status.PLAYED)
    clock.now = at(19)
    wait_for(app, lambda: statuses(app)[1:4] == [Status.PLAYED] * 3)

    assert heard(output)[2:] == [
        CHIME,
        end_text(AYSE),
        start_text(AYSE),
        start_text(MEHMET),
    ]
    assert app.last_announcement == LastAnnouncement(
        at(19),
        ((Kind.END, AYSE), (Kind.START, AYSE), (Kind.START, MEHMET)),
        Status.PLAYED,
    )


def test_end_of_a_session_is_not_announced_while_switched_off(
    app: App,
    schedule: Path,
    settings_file: Path,
    output: FakeOutput,
    clock: FakeClock,
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)

    app.set_end_announcements(False)

    assert load_settings(settings_file).settings.end_announcements is False
    assert [row.kind for row in app.rows()] == [Kind.START, Kind.START]
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app) == [Status.PLAYED] * 2)
    clock.now = at(19)
    let_time_pass(app)
    assert len(output.played) == 3

    clock.now = at(19, 1)
    app.set_end_announcements(True)

    assert not app.generating  # its speech was kept
    assert [(row.kind, row.status) for row in app.rows()] == [
        (Kind.START, Status.PLAYED),
        (Kind.START, Status.PLAYED),
        (Kind.END, Status.PASSED),
        (Kind.END, Status.PASSED),
    ]


def test_custom_announcement_in_the_minute_of_an_end_is_read_after_it(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    wait_for_speech(app)

    assert app.timetable.conflicts == ()
    assert [(row.time, row.kind) for row in app.rows()] == [
        (SIX_PM, Kind.START),
        (SIX_PM, Kind.START),
        (SEVEN_PM, Kind.END),
        (SEVEN_PM, Kind.END),
        (SEVEN_PM, Kind.CUSTOM),
    ]
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[:2] == [Status.PLAYED] * 2)
    clock.now = at(19)
    wait_for(app, lambda: statuses(app) == [Status.PLAYED] * 5)

    assert heard(output)[3:] == [
        CHIME,
        end_text(AYSE),
        end_text(MEHMET),
        WATER.text,
    ]
    assert app.last_announcement == LastAnnouncement(
        at(19),
        ((Kind.END, AYSE), (Kind.END, MEHMET), (Kind.CUSTOM, WATER.text)),
        Status.PLAYED,
    )


def test_each_row_follows_the_reading_of_its_athlete(
    app: App, output: FakeOutput, clock: FakeClock, tmp_path: Path
) -> None:
    trio = [(name, "Pazartesi", "18:00") for name in (AYSE, MEHMET, ZEYNEP)]
    app.choose_schedule(write_schedule(tmp_path / "trio.xlsx", trio))
    wait_for_speech(app)
    app.start()
    ends = [Status.NEXT] * 3

    clock.now = at(18)
    seen = statuses_while_sounding(app, output, files=4)

    assert seen == [
        (CHIME, [Status.QUEUED, Status.QUEUED, Status.QUEUED, *ends]),
        (start_text(AYSE), [Status.PLAYING, Status.QUEUED, Status.QUEUED, *ends]),
        (start_text(MEHMET), [Status.PLAYED, Status.PLAYING, Status.QUEUED, *ends]),
        (start_text(ZEYNEP), [Status.PLAYED, Status.PLAYED, Status.PLAYING, *ends]),
    ]
    wait_for(app, lambda: statuses(app) == [Status.PLAYED] * 3 + ends)
    assert app.last_announcement == LastAnnouncement(
        at(18),
        ((Kind.START, AYSE), (Kind.START, MEHMET), (Kind.START, ZEYNEP)),
        Status.PLAYED,
    )


def test_file_that_cannot_be_played_fails_only_the_athlete_it_is_for(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock, tmp_path: Path
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    output.unplayable = {speech_file(tmp_path / "audio", start_text(AYSE))}
    app.start()

    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[:2] == [Status.NO_OUTPUT, Status.PLAYED])

    assert [shown(row) for row in app.rows()] == [
        "Hata (ses çıkışı)",
        "Okundu",
        "Sıradaki",
        "Sıradaki",
    ]
    assert heard(output) == [CHIME, start_text(MEHMET)]
    assert app.last_announcement == LastAnnouncement(
        at(18), ((Kind.START, AYSE), (Kind.START, MEHMET)), Status.NO_OUTPUT
    )
    assert row_of(app, Kind.START, AYSE).replayable


def test_athlete_heard_is_read_again_only_once_the_rest_of_the_minute_is_read(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    release = held_sounding(output, start_text(MEHMET))
    clock.now = at(18)
    wait_for(app, lambda: status_of(app, Kind.START, MEHMET) is Status.PLAYING)

    first = row_of(app, Kind.START, AYSE)
    assert (first.status, first.in_group, first.replayable) == (
        Status.PLAYED,
        True,
        False,
    )
    assert not app.replay(first)
    release.set()
    wait_for(app, lambda: statuses(app)[:2] == [Status.PLAYED] * 2)

    first = row_of(app, Kind.START, AYSE)
    assert (first.in_group, first.replayable) == (False, True)
    assert app.replay(first)
    wait_for(app, lambda: row_of(app, Kind.START, AYSE).replayable)
    assert status_of(app, Kind.START, AYSE) is Status.PLAYED
    assert heard(output) == [
        CHIME,
        start_text(AYSE),
        start_text(MEHMET),
        CHIME,
        start_text(AYSE),
    ]


def test_athlete_without_speech_is_not_shown_as_read_while_the_next_one_is(
    app: App,
    schedule: Path,
    provider: FakeProvider,
    output: FakeOutput,
    clock: FakeClock,
) -> None:
    provider.unspeakable = {start_text(AYSE)}
    app.choose_schedule(schedule)
    wait_for_speech(app)
    assert app.unspoken == (start_text(AYSE),)
    app.start()
    release = held_sounding(output, start_text(MEHMET))
    clock.now = at(18)
    wait_for(app, lambda: status_of(app, Kind.START, MEHMET) is Status.PLAYING)

    unspoken = row_of(app, Kind.START, AYSE)
    assert unspoken.status is not Status.PLAYED
    assert not unspoken.replayable
    release.set()

    wait_for(app, lambda: statuses(app)[:2] == [Status.NO_SPEECH, Status.PLAYED])
    assert heard(output) == [CHIME, start_text(MEHMET)]


def test_new_templates_are_stored_and_spoken(
    app: App, schedule: Path, settings_file: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)

    app.set_templates(NEW_START, NEW_END)
    wait_for_speech(app)

    stored = load_settings(settings_file).settings
    assert (stored.template, stored.end_template) == (NEW_START, NEW_END)
    assert (app.spoken_template, app.spoken_end_template) == (NEW_START, NEW_END)
    assert [slot.texts for slot in app.timetable.slots_on(MONDAY)] == [
        (render(NEW_START, AYSE), render(NEW_START, MEHMET)),
        (render(NEW_END, AYSE), render(NEW_END, MEHMET)),
    ]
    for template in (NEW_START, NEW_END):
        assert (render(template, ZEYNEP), app.settings.voice) in provider.calls
    assert app.unspoken == ()


def test_athletes_keep_both_old_templates_until_both_new_ones_have_speech(
    app: App,
    schedule: Path,
    provider: FakeProvider,
    output: FakeOutput,
    clock: FakeClock,
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    provider.unspeakable = {render(NEW_END, ZEYNEP)}

    app.set_templates(NEW_START, NEW_END)
    wait_for_speech(app)

    # The new start is not used without the end.
    assert (app.spoken_template, app.spoken_end_template) == (
        DEFAULT_TEMPLATE,
        DEFAULT_END_TEMPLATE,
    )
    assert app.unspoken == (render(NEW_END, ZEYNEP),)
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[:2] == [Status.PLAYED] * 2)
    assert heard(output) == [CHIME, start_text(AYSE), start_text(MEHMET)]

    provider.unspeakable = set()
    app.refresh()
    wait_for_speech(app)

    assert (app.spoken_template, app.spoken_end_template) == (NEW_START, NEW_END)
    assert statuses(app)[:2] == [Status.PLAYED] * 2
    clock.now = at(19)
    wait_for(app, lambda: statuses(app)[2:] == [Status.PLAYED] * 2)
    assert heard(output)[3:] == [
        CHIME,
        render(NEW_END, AYSE),
        render(NEW_END, MEHMET),
    ]


def test_planned_sentence_follows_the_templates_in_the_settings_not_the_spoken(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    provider.offline = True
    app.set_templates(NEW_START, NEW_END)
    wait_for_speech(app)
    assert app.spoken_template == DEFAULT_TEMPLATE
    assert row_of(app, Kind.START, AYSE).text == start_text(AYSE)

    start = app.editing(row_of(app, Kind.START, AYSE))
    end = app.editing(row_of(app, Kind.END, AYSE))

    assert start is not None and end is not None
    assert start.planned_text == render(NEW_START, AYSE)
    assert end.planned_text == render(NEW_END, AYSE)
    # The sentence still spoken is no longer the plan, so keeping it is a change.
    app.edit(row_of(app, Kind.START, AYSE), SIX_PM, start_text(AYSE))
    assert app.changes == (
        Change(MONDAY, at(18), Kind.START, AYSE, text=start_text(AYSE)),
    )
    wait_for_speech(app)
    provider.offline = False
    app.refresh()
    wait_for_speech(app)
    assert app.spoken_template == NEW_START
    assert [(row.text, row.edited) for row in app.rows()[:2]] == [
        (start_text(AYSE), True),
        (render(NEW_START, MEHMET), False),
    ]


def test_outcome_stays_with_its_athlete_when_the_template_changes_afterwards(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[:2] == [Status.PLAYED] * 2)

    app.set_templates(NEW_START, NEW_END)
    wait_for_speech(app)

    assert [(row.text, row.status) for row in app.rows()] == [
        (render(NEW_START, AYSE), Status.PLAYED),
        (render(NEW_START, MEHMET), Status.PLAYED),
        (render(NEW_END, AYSE), Status.NEXT),
        (render(NEW_END, MEHMET), Status.NEXT),
    ]
    assert row_of(app, Kind.START, AYSE).replayable
    let_time_pass(app)
    assert len(output.played) == 3


def test_restart_within_the_minute_of_a_delayed_announcement_does_not_repeat_it(
    make_app: Callable[[], App],
    schedule: Path,
    settings_file: Path,
    output: FakeOutput,
    clock: FakeClock,
) -> None:
    save_settings(Settings(schedule_path=schedule, delay=1), settings_file)
    first = make_app()
    first.start()
    wait_for_speech(first)
    clock.now = at(18, 1)
    wait_for(first, lambda: statuses(first)[:2] == [Status.PLAYED] * 2)
    first.stop()
    played = len(output.played)

    clock.now = at(18, 1, 20)
    app = make_app()
    app.start()
    let_time_pass(app)

    assert len(output.played) == played
    assert statuses(app) == [Status.PASSED] * 2 + [Status.NEXT] * 2
    clock.now = at(19, 1)
    wait_for(app, lambda: statuses(app)[2:] == [Status.PLAYED] * 2)
    assert heard(output)[played:] == [CHIME, end_text(AYSE), end_text(MEHMET)]


def test_restart_in_the_minute_that_a_delayed_announcement_was_set_for_keeps_it(
    make_app: Callable[[], App],
    schedule: Path,
    settings_file: Path,
    output: FakeOutput,
    clock: FakeClock,
) -> None:
    save_settings(Settings(schedule_path=schedule, delay=1), settings_file)
    first = make_app()
    first.start()
    wait_for_speech(first)
    first.stop()

    clock.now = at(18, 0, 30)
    app = make_app()
    app.start()
    let_time_pass(app)
    assert statuses(app) == [Status.NEXT] * 2 + [Status.WAITING] * 2
    clock.now = at(18, 1)
    wait_for(app, lambda: statuses(app)[:2] == [Status.PLAYED] * 2)

    assert len(output.played) == 3


def test_custom_announcement_in_the_minute_that_the_app_starts_in_is_passed(
    make_app: Callable[[], App],
    settings_file: Path,
    output: FakeOutput,
    clock: FakeClock,
) -> None:
    save_settings(Settings(custom_announcements=(WATER,)), settings_file)
    clock.now = at(19, 0, 10)

    app = make_app()
    app.start()
    wait_for_speech(app)
    let_time_pass(app)

    custom = row_of(app, Kind.CUSTOM)
    assert (custom.status, custom.replayable) == (Status.PASSED, True)
    assert output.played == []


def test_custom_announcement_slept_through_is_shown_as_such(
    app: App, output: FakeOutput, clock: FakeClock
) -> None:
    app.save_announcement(WATER)
    wait_for_speech(app)
    app.start()

    clock.now = at(19, 5)

    wait_for(app, lambda: status_of(app, Kind.CUSTOM) is Status.ASLEEP)
    assert output.played == []
    assert row_of(app, Kind.CUSTOM).replayable


def test_skipped_session_is_announced_neither_at_its_start_nor_at_its_end(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()

    skip_everyone(app)

    assert [(row.kind, row.status, row.skippable) for row in app.rows()] == [
        (Kind.START, Status.SKIPPING, True),
        (Kind.START, Status.SKIPPING, True),
        (Kind.END, Status.SKIPPING, True),
        (Kind.END, Status.SKIPPING, True),
    ]
    assert load_changes(app.changes_file, MONDAY) == (
        Change(MONDAY, at(18), Kind.START, AYSE, skipped=True),
        Change(MONDAY, at(19), Kind.END, AYSE, skipped=True),
        Change(MONDAY, at(18), Kind.START, MEHMET, skipped=True),
        Change(MONDAY, at(19), Kind.END, MEHMET, skipped=True),
    )
    clock.now = at(18)
    let_time_pass(app)
    clock.now = at(19)
    let_time_pass(app)
    clock.now = at(19, 1)

    assert output.played == []
    assert statuses(app) == [Status.SKIPPED] * 4


def test_athlete_skipped_leaves_the_other_of_the_same_minute_to_be_read(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()

    app.skip(row_of(app, Kind.START, AYSE))

    assert [
        (row.kind, name_of(row), row.status, row.skippable) for row in app.rows()
    ] == [
        (Kind.START, AYSE, Status.SKIPPING, True),
        (Kind.START, MEHMET, Status.NEXT, True),
        (Kind.END, AYSE, Status.SKIPPING, True),
        (Kind.END, MEHMET, Status.WAITING, True),
    ]
    assert app.changes == (
        Change(MONDAY, at(18), Kind.START, AYSE, skipped=True),
        Change(MONDAY, at(19), Kind.END, AYSE, skipped=True),
    )
    clock.now = at(18)
    wait_for(app, lambda: status_of(app, Kind.START, MEHMET) is Status.PLAYED)
    clock.now = at(19)
    wait_for(app, lambda: status_of(app, Kind.END, MEHMET) is Status.PLAYED)
    clock.now = at(19, 1)

    assert heard(output) == [CHIME, start_text(MEHMET), CHIME, end_text(MEHMET)]
    assert statuses(app) == [
        Status.SKIPPED,
        Status.PLAYED,
        Status.SKIPPED,
        Status.PLAYED,
    ]
    assert app.last_announcement == LastAnnouncement(
        at(19), ((Kind.END, MEHMET),), Status.PLAYED
    )


def test_skip_is_kept_through_a_restart_for_that_day_only(
    make_app: Callable[[], App], schedule: Path, clock: FakeClock
) -> None:
    first = make_app()
    first.choose_schedule(schedule)
    first.skip(row_of(first, Kind.START, AYSE))
    first.stop()

    again = make_app()
    again.start()

    assert statuses(again) == [
        Status.SKIPPING,
        Status.NEXT,
        Status.SKIPPING,
        Status.WAITING,
    ]
    clock.now = at(8) + 7 * DAY  # the next Monday
    assert statuses(again) == [Status.NEXT] * 2 + [Status.WAITING] * 2
    again.stop()

    clock.now = at(8) + DAY
    next_day = make_app()
    next_day.start()
    assert next_day.changes == ()
    next_day.skip(row_of(next_day, Kind.START, ZEYNEP))
    assert {change.day for change in load_changes(next_day.changes_file, MONDAY)} == {
        TUESDAY
    }


def test_undoing_the_skip_of_a_start_brings_its_end_back_too(
    app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    app.skip(row_of(app, Kind.START, AYSE))

    app.skip(row_of(app, Kind.START, AYSE), skipped=False)

    assert statuses(app) == [Status.NEXT] * 2 + [Status.WAITING] * 2
    assert app.changes == ()
    assert load_changes(app.changes_file, MONDAY) == ()


def test_end_can_be_skipped_and_brought_back_by_itself(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.skip(row_of(app, Kind.START, AYSE))

    app.skip(row_of(app, Kind.END, AYSE), skipped=False)

    assert statuses(app) == [
        Status.SKIPPING,
        Status.NEXT,
        Status.WAITING,
        Status.WAITING,
    ]
    assert app.changes == (Change(MONDAY, at(18), Kind.START, AYSE, skipped=True),)

    app.skip(row_of(app, Kind.START, AYSE), skipped=False)
    app.skip(row_of(app, Kind.END, AYSE))

    assert statuses(app) == [
        Status.NEXT,
        Status.NEXT,
        Status.SKIPPING,
        Status.WAITING,
    ]
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[:2] == [Status.PLAYED] * 2)
    clock.now = at(19)
    wait_for(app, lambda: status_of(app, Kind.END, MEHMET) is Status.PLAYED)
    assert heard(output)[3:] == [CHIME, end_text(MEHMET)]


def test_skip_undone_within_the_minute_of_its_time_is_announced_at_once(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.skip(row_of(app, Kind.START, AYSE))
    app.start()
    clock.now = at(18, 0, 10)
    wait_for(app, lambda: status_of(app, Kind.START, MEHMET) is Status.PLAYED)
    skipped = row_of(app, Kind.START, AYSE)
    assert (skipped.status, skipped.skippable, skipped.editable) == (
        Status.SKIPPING,
        True,
        False,
    )
    assert heard(output) == [CHIME, start_text(MEHMET)]

    app.skip(skipped, skipped=False)

    wait_for(app, lambda: statuses(app) == [Status.PLAYED] * 2 + [Status.NEXT] * 2)
    assert heard(output)[2:] == [CHIME, start_text(AYSE)]


def test_skip_can_no_longer_be_undone_at_the_end_of_its_minute(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.skip(row_of(app, Kind.START, AYSE))
    clock.now = at(18, 0, 29)

    assert not row_of(app, Kind.START, AYSE).skippable
    with pytest.raises(ChangeError) as error:
        app.skip(row_of(app, Kind.START, AYSE), skipped=False)

    assert error.value.problem is ChangeProblem.GONE
    clock.now = at(18, 0, 31)
    assert statuses(app) == [
        Status.SKIPPED,
        Status.PASSED,
        Status.SKIPPING,
        Status.NEXT,
    ]


def test_restart_within_the_minute_of_a_skipped_row_keeps_it_skipped_until_undone(
    make_app: Callable[[], App], schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    first = make_app()
    first.choose_schedule(schedule)
    wait_for_speech(first)
    first.skip(row_of(first, Kind.START, AYSE))
    first.stop()

    clock.now = at(18, 0, 10)
    app = make_app()
    app.start()
    let_time_pass(app)

    skipped = row_of(app, Kind.START, AYSE)
    assert (skipped.status, skipped.skippable) == (Status.SKIPPING, True)
    # The other athlete's minute had begun before the app started.
    assert status_of(app, Kind.START, MEHMET) is Status.PASSED
    assert output.played == []
    app.skip(skipped, skipped=False)
    wait_for(app, lambda: status_of(app, Kind.START, AYSE) is Status.PLAYED)
    assert statuses(app) == [
        Status.PLAYED,
        Status.PASSED,
        Status.NEXT,
        Status.NEXT,
    ]
    assert heard(output) == [CHIME, start_text(AYSE)]


def test_start_skipped_while_ends_are_switched_off_has_its_end_skipped_when_on(
    app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    app.set_end_announcements(False)
    off = app.editing(row_of(app, Kind.START, AYSE))
    assert off is not None
    assert off.end is None

    app.skip(row_of(app, Kind.START, AYSE))
    app.set_end_announcements(True)

    assert [(row.kind, name_of(row), row.status) for row in app.rows()] == [
        (Kind.START, AYSE, Status.SKIPPING),
        (Kind.START, MEHMET, Status.NEXT),
        (Kind.END, AYSE, Status.SKIPPING),
        (Kind.END, MEHMET, Status.WAITING),
    ]
    on = app.editing(row_of(app, Kind.START, AYSE))
    assert on is not None
    assert on.end == SEVEN_PM
    app.skip(row_of(app, Kind.START, AYSE), skipped=False)
    assert statuses(app) == [Status.NEXT] * 2 + [Status.WAITING] * 2


def test_row_can_no_longer_be_changed_shortly_before_its_minute(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    shown = row_of(app, Kind.START, AYSE)  # shown a moment ago
    assert (shown.editable, shown.skippable) == (True, True)
    clock.now = at(17, 59, 59)

    current = row_of(app, Kind.START, AYSE)
    assert (current.editable, current.skippable) == (False, False)
    assert app.editing(current) is None
    with pytest.raises(ChangeError) as skipping:
        app.skip(shown)
    with pytest.raises(ChangeError) as editing:
        app.edit(shown, datetime.time(18, 30), shown.text)

    assert skipping.value.problem is editing.value.problem is ChangeProblem.GONE
    assert app.changes == ()
    app.skip(row_of(app, Kind.END, AYSE))  # whose time has not come
    assert statuses(app) == [
        Status.NEXT,
        Status.NEXT,
        Status.SKIPPING,
        Status.WAITING,
    ]


def test_row_shown_before_its_time_came_close_is_not_offered_for_editing(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    shown = row_of(app, Kind.START, AYSE)
    clock.now = at(17, 59, 59)

    assert app.editing(shown) is None


def test_row_moved_to_another_time_is_announced_then_and_only_then(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.set_delay(1)
    wait_for_speech(app)
    app.start()
    editing = app.editing(row_of(app, Kind.START, AYSE))
    assert editing is not None
    assert (editing.planned, editing.time, editing.end) == (
        datetime.time(18, 1),
        datetime.time(18, 1),
        datetime.time(19, 1),
    )

    app.edit(row_of(app, Kind.START, AYSE), datetime.time(18, 30), editing.text)

    # The delay is not added to the time chosen.
    assert [(row.time, row.kind, name_of(row), row.edited) for row in app.rows()] == [
        (datetime.time(18, 1), Kind.START, MEHMET, False),
        (datetime.time(18, 30), Kind.START, AYSE, True),
        (datetime.time(19, 1), Kind.END, AYSE, False),
        (datetime.time(19, 1), Kind.END, MEHMET, False),
    ]
    assert load_changes(app.changes_file, MONDAY) == (
        Change(MONDAY, at(18), Kind.START, AYSE, time=datetime.time(18, 30)),
    )
    moved = app.editing(row_of(app, Kind.START, AYSE))
    assert moved is not None
    assert (moved.planned, moved.time) == (datetime.time(18, 1), datetime.time(18, 30))
    clock.now = at(18, 1)
    wait_for(app, lambda: status_of(app, Kind.START, MEHMET) is Status.PLAYED)
    let_time_pass(app)
    assert heard(output) == [CHIME, start_text(MEHMET)]
    clock.now = at(18, 30)
    wait_for(app, lambda: status_of(app, Kind.START, AYSE) is Status.PLAYED)
    assert heard(output)[2:] == [CHIME, start_text(AYSE)]


def test_row_cannot_be_moved_to_a_time_that_has_come(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    clock.now = at(9, 30)
    start = row_of(app, Kind.START, AYSE)

    for gone in (datetime.time(9, 0), datetime.time(9, 30)):
        with pytest.raises(ChangeError) as error:
            app.edit(start, gone, start.text)
        assert error.value.problem is ChangeProblem.PAST
    assert app.changes == ()

    app.edit(start, datetime.time(9, 31), start.text)
    assert row_of(app, Kind.START, AYSE).time == datetime.time(9, 31)
    assert row_of(app, Kind.START, MEHMET).time == SIX_PM


def test_moved_row_cannot_go_back_to_its_planned_time_once_that_has_come(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    app.edit(row_of(app, Kind.START, AYSE), datetime.time(18, 30), start_text(AYSE))
    moved = app.changes
    clock.now = at(18, 10)

    with pytest.raises(ChangeError) as error:
        app.edit(row_of(app, Kind.START, AYSE), SIX_PM, start_text(AYSE))

    assert error.value.problem is ChangeProblem.PAST
    assert app.changes == moved
    start = row_of(app, Kind.START, AYSE)
    assert (start.time, start.status, start.edited) == (
        datetime.time(18, 30),
        Status.NEXT,
        True,
    )
    clock.now = at(18, 30)
    wait_for(app, lambda: status_of(app, Kind.START, AYSE) is Status.PLAYED)
    assert heard(output) == [CHIME, start_text(AYSE)]


@pytest.mark.parametrize(
    ("kind", "text", "problem"),
    [
        (Kind.START, "   ", ChangeProblem.NO_TEXT),
        (Kind.START, NEW_START, ChangeProblem.NO_SPEECH),
        (Kind.END, "Güle güle.", ChangeProblem.NO_SPEECH),
        (Kind.CUSTOM, " ", ChangeProblem.NO_TEXT),
        (Kind.CUSTOM, CLOSING, ChangeProblem.NO_SPEECH),
    ],
    ids=[
        "no text",
        "no speech",
        "no speech for a sentence without the name",
        "no custom text",
        "no speech for a custom text",
    ],
)
def test_change_that_cannot_be_made_is_refused_and_changes_nothing(
    app: App, schedule: Path, kind: Kind, text: str, problem: ChangeProblem
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    wait_for_speech(app)
    row = row_of(app, kind)

    with pytest.raises(ChangeError) as error:
        app.edit(row, row.time, text)

    assert error.value.problem is problem
    assert app.changes == ()
    assert not app.changes_file.exists()
    assert [row.edited for row in app.rows()] == [False] * 5


def test_new_sentence_of_one_athlete_is_used_once_its_speech_is_made_and_is_read(
    app: App,
    schedule: Path,
    provider: FakeProvider,
    output: FakeOutput,
    clock: FakeClock,
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    editing = app.editing(row_of(app, Kind.START, AYSE))
    assert editing is not None
    texts = [app.text_of(editing, NEW_START)]
    assert texts == [render(NEW_START, AYSE)]

    provider.offline = True
    offline = app.make_speech(texts)
    wait_for(app, lambda: offline.done)
    assert not offline.spoken
    with pytest.raises(ChangeError) as error:
        app.edit(row_of(app, Kind.START, AYSE), SIX_PM, NEW_START)
    assert error.value.problem is ChangeProblem.NO_SPEECH

    provider.offline = False
    online = app.make_speech(texts)
    wait_for(app, lambda: online.done)
    assert online.spoken
    app.edit(row_of(app, Kind.START, AYSE), SIX_PM, NEW_START)

    assert not app.generating
    assert app.changes == (
        Change(MONDAY, at(18), Kind.START, AYSE, text=render(NEW_START, AYSE)),
    )
    edited = app.editing(row_of(app, Kind.START, AYSE))
    assert edited is not None
    assert (edited.planned_text, edited.text) == (
        start_text(AYSE),
        render(NEW_START, AYSE),
    )
    assert [(row.text, row.edited) for row in app.rows()] == [
        (render(NEW_START, AYSE), True),
        (start_text(MEHMET), False),
        (end_text(AYSE), False),
        (end_text(MEHMET), False),
    ]
    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[:2] == [Status.PLAYED] * 2)
    assert heard(output) == [CHIME, render(NEW_START, AYSE), start_text(MEHMET)]
    clock.now = at(19)
    wait_for(app, lambda: statuses(app)[2:] == [Status.PLAYED] * 2)
    assert heard(output)[3:] == [CHIME, end_text(AYSE), end_text(MEHMET)]


def test_sentence_of_an_athlete_may_leave_the_name_out(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    goodbye = "Görüşmek üzere."
    speak_ahead(app, [goodbye])

    app.edit(row_of(app, Kind.END, MEHMET), SEVEN_PM, goodbye)

    assert [row.text for row in app.rows() if row.kind is Kind.END] == [
        end_text(AYSE),
        goodbye,
    ]
    clock.now = at(19)
    wait_for(app, lambda: statuses(app)[2:] == [Status.PLAYED] * 2)
    assert heard(output) == [CHIME, end_text(AYSE), goodbye]


def test_speech_made_ahead_is_made_at_the_speed_in_use_and_at_the_one_chosen(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    provider.offline = True
    app.set_speed(0.9)
    wait_for_speech(app)
    assert (app.spoken_speed, app.settings.speed) == (1.0, 0.9)
    provider.offline = False

    speak_ahead(app, [CLOSING])

    assert sorted(
        speed
        for (text, _), speed in zip(provider.calls, provider.speeds, strict=True)
        if text == CLOSING
    ) == [0.9, 1.0]


def test_custom_announcement_reworded_for_today_is_read_with_its_new_text(
    app: App, settings_file: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.save_announcement(WATER)
    wait_for_speech(app)
    app.start()
    editing = app.editing(row_of(app, Kind.CUSTOM))
    assert editing is not None
    speak_ahead(app, [app.text_of(editing, CLOSING)])

    app.edit(row_of(app, Kind.CUSTOM), SEVEN_PM, CLOSING)

    custom = row_of(app, Kind.CUSTOM)
    assert (custom.time, custom.text, custom.edited) == (SEVEN_PM, CLOSING, True)
    assert app.settings.custom_announcements == (WATER,)
    assert load_settings(settings_file).settings.custom_announcements == (WATER,)
    clock.now = at(19)
    wait_for(app, lambda: statuses(app) == [Status.PLAYED])
    assert heard(output) == [CHIME, CLOSING]


def test_custom_announcement_reworded_with_a_placeholder_is_read_as_typed(
    app: App, output: FakeOutput, clock: FakeClock
) -> None:
    app.save_announcement(WATER)
    wait_for_speech(app)
    app.start()
    typed = "Su içmeyi unutmayın, {ad_soyad}."
    editing = app.editing(row_of(app, Kind.CUSTOM))
    assert editing is not None
    assert app.text_of(editing, typed) == typed
    speak_ahead(app, [typed])

    app.edit(row_of(app, Kind.CUSTOM), SEVEN_PM, typed)

    assert load_changes(app.changes_file, MONDAY) == (
        Change(MONDAY, at(19), Kind.CUSTOM, text=typed),
    )
    custom = row_of(app, Kind.CUSTOM)
    assert (custom.text, custom.edited) == (typed, True)
    edited = app.editing(custom)
    assert edited is not None
    assert (edited.planned_text, edited.text) == (WATER.text, typed)
    clock.now = at(19)
    wait_for(app, lambda: statuses(app) == [Status.PLAYED])
    assert heard(output) == [CHIME, typed]


def test_change_back_to_the_plan_is_no_change(app: App, schedule: Path) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    speak_ahead(app, [render(NEW_START, AYSE)])
    app.edit(row_of(app, Kind.START, AYSE), datetime.time(18, 30), NEW_START)
    assert load_changes(app.changes_file, MONDAY) == (
        Change(
            MONDAY,
            at(18),
            Kind.START,
            AYSE,
            time=datetime.time(18, 30),
            text=render(NEW_START, AYSE),
        ),
    )

    app.edit(row_of(app, Kind.START, AYSE), SIX_PM, start_text(AYSE))

    assert app.changes == ()
    assert load_changes(app.changes_file, MONDAY) == ()
    assert [(row.time, row.text, row.edited) for row in app.rows()] == [
        (SIX_PM, start_text(AYSE), False),
        (SIX_PM, start_text(MEHMET), False),
        (SEVEN_PM, end_text(AYSE), False),
        (SEVEN_PM, end_text(MEHMET), False),
    ]


def test_text_that_says_the_planned_sentence_through_the_placeholder_is_no_change(
    app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)

    app.edit(row_of(app, Kind.START, AYSE), SIX_PM, DEFAULT_TEMPLATE)

    assert app.changes == ()
    assert not row_of(app, Kind.START, AYSE).edited


def test_stored_sentence_with_a_placeholder_is_offered_for_editing_as_said(
    make_app: Callable[[], App], schedule: Path, settings_file: Path
) -> None:
    typed = "Tekrar hoş geldin {ad_soyad}."
    welcome = "Tekrar hoş geldin Mehmet Demir."
    save_settings(Settings(schedule_path=schedule), settings_file)
    save_changes(
        [Change(MONDAY, at(18), Kind.START, MEHMET, text=typed)],
        settings_file.with_name("changes.json"),
    )

    app = make_app()
    app.refresh()
    wait_for_speech(app)

    row = row_of(app, Kind.START, MEHMET)
    assert (row.text, row.edited) == (welcome, True)
    editing = app.editing(row)
    assert editing is not None
    assert (editing.planned_text, editing.text) == (start_text(MEHMET), welcome)
    app.edit(row, SIX_PM, editing.text)
    assert (row_of(app, Kind.START, MEHMET).text, app.changes) == (
        welcome,
        (Change(MONDAY, at(18), Kind.START, MEHMET, text=welcome),),
    )


def test_skipped_row_moved_to_another_time_stays_skipped(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    app.skip(row_of(app, Kind.START, AYSE))

    app.edit(row_of(app, Kind.START, AYSE), datetime.time(18, 30), start_text(AYSE))

    start = row_of(app, Kind.START, AYSE)
    assert (start.time, start.status, start.edited) == (
        datetime.time(18, 30),
        Status.SKIPPING,
        True,
    )
    assert set(load_changes(app.changes_file, MONDAY)) == {
        Change(
            MONDAY, at(18), Kind.START, AYSE, skipped=True, time=datetime.time(18, 30)
        ),
        Change(MONDAY, at(19), Kind.END, AYSE, skipped=True),
    }
    clock.now = at(18)
    wait_for(app, lambda: status_of(app, Kind.START, MEHMET) is Status.PLAYED)
    clock.now = at(18, 30)
    let_time_pass(app)
    clock.now = at(19)
    wait_for(app, lambda: status_of(app, Kind.END, MEHMET) is Status.PLAYED)
    clock.now = at(19, 1)
    assert heard(output) == [CHIME, start_text(MEHMET), CHIME, end_text(MEHMET)]
    assert [(row.kind, name_of(row), row.status) for row in app.rows()] == [
        (Kind.START, MEHMET, Status.PLAYED),
        (Kind.START, AYSE, Status.SKIPPED),
        (Kind.END, AYSE, Status.SKIPPED),
        (Kind.END, MEHMET, Status.PLAYED),
    ]


def test_skipping_and_bringing_back_a_start_keeps_what_was_changed_about_it(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    app.edit(row_of(app, Kind.START, AYSE), datetime.time(18, 30), start_text(AYSE))
    app.edit(row_of(app, Kind.END, AYSE), datetime.time(19, 15), end_text(AYSE))
    edited = app.changes

    app.skip(row_of(app, Kind.START, AYSE))
    app.skip(row_of(app, Kind.START, AYSE), skipped=False)

    assert app.changes == edited
    assert [
        (row.time, row.kind, name_of(row), row.status, row.edited) for row in app.rows()
    ] == [
        (SIX_PM, Kind.START, MEHMET, Status.NEXT, False),
        (datetime.time(18, 30), Kind.START, AYSE, Status.WAITING, True),
        (SEVEN_PM, Kind.END, MEHMET, Status.WAITING, False),
        (datetime.time(19, 15), Kind.END, AYSE, Status.WAITING, True),
    ]
    clock.now = at(18)
    wait_for(app, lambda: status_of(app, Kind.START, MEHMET) is Status.PLAYED)
    clock.now = at(18, 30)
    wait_for(app, lambda: status_of(app, Kind.START, AYSE) is Status.PLAYED)
    assert heard(output) == [CHIME, start_text(MEHMET), CHIME, start_text(AYSE)]


def test_start_skipped_or_changed_keeps_what_was_changed_about_its_end(
    app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    end = app.editing(row_of(app, Kind.END, AYSE))
    assert end is not None
    speak_ahead(app, [app.text_of(end, NEW_END)])
    app.edit(row_of(app, Kind.END, AYSE), datetime.time(19, 15), NEW_END)

    app.skip(row_of(app, Kind.START, AYSE))
    app.edit(row_of(app, Kind.START, AYSE), datetime.time(18, 30), start_text(AYSE))

    assert set(app.changes) == {
        Change(
            MONDAY, at(18), Kind.START, AYSE, skipped=True, time=datetime.time(18, 30)
        ),
        Change(
            MONDAY,
            at(19),
            Kind.END,
            AYSE,
            skipped=True,
            time=datetime.time(19, 15),
            text=render(NEW_END, AYSE),
        ),
    }
    assert load_changes(app.changes_file, MONDAY) == app.changes
    assert [(row.time, row.kind, row.text, row.status) for row in app.rows()] == [
        (SIX_PM, Kind.START, start_text(MEHMET), Status.NEXT),
        (datetime.time(18, 30), Kind.START, start_text(AYSE), Status.SKIPPING),
        (SEVEN_PM, Kind.END, end_text(MEHMET), Status.WAITING),
        (datetime.time(19, 15), Kind.END, render(NEW_END, AYSE), Status.SKIPPING),
    ]


def test_start_changed_after_its_end_came_leaves_that_end_alone(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.edit(row_of(app, Kind.START, AYSE), datetime.time(19, 30), start_text(AYSE))
    clock.now = at(19, 10)
    editing = app.editing(row_of(app, Kind.START, AYSE))
    assert editing is not None
    assert editing.end is None

    app.skip(row_of(app, Kind.START, AYSE))
    assert [change.kind for change in app.changes] == [Kind.START]
    app.skip(row_of(app, Kind.START, AYSE), skipped=False)
    speak_ahead(app, [render(NEW_START, AYSE)])
    app.edit(row_of(app, Kind.START, AYSE), datetime.time(19, 30), NEW_START)

    assert load_changes(app.changes_file, MONDAY) == (
        Change(
            MONDAY,
            at(18),
            Kind.START,
            AYSE,
            time=datetime.time(19, 30),
            text=render(NEW_START, AYSE),
        ),
    )
    assert [(row.time, row.kind, row.text) for row in app.rows()] == [
        (SIX_PM, Kind.START, start_text(MEHMET)),
        (SEVEN_PM, Kind.END, end_text(AYSE)),
        (SEVEN_PM, Kind.END, end_text(MEHMET)),
        (datetime.time(19, 30), Kind.START, render(NEW_START, AYSE)),
    ]


def test_editing_tells_what_one_athlete_and_a_custom_announcement_are_told(
    app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(WATER)

    assert app.editing(row_of(app, Kind.START, MEHMET)) == Editing(
        Kind.START,
        SIX_PM,
        SIX_PM,
        start_text(MEHMET),
        start_text(MEHMET),
        MEHMET,
        SEVEN_PM,
    )
    assert app.editing(row_of(app, Kind.END, AYSE)) == Editing(
        Kind.END, SEVEN_PM, SEVEN_PM, end_text(AYSE), end_text(AYSE), AYSE, None
    )
    assert app.editing(row_of(app, Kind.CUSTOM)) == Editing(
        Kind.CUSTOM, SEVEN_PM, SEVEN_PM, WATER.text, WATER.text, "", None
    )


def replaced(app: App, announcement: CustomAnnouncement) -> None:
    app.save_announcement(announcement, replacing=0)


def deleted_and_added(app: App, announcement: CustomAnnouncement) -> None:
    app.delete_announcement(0)
    app.save_announcement(announcement)


@pytest.mark.parametrize(
    "put_in_its_place", [replaced, deleted_and_added], ids=["replaced", "deleted"]
)
def test_custom_announcement_given_a_new_text_or_deleted_loses_todays_change(
    app: App,
    schedule: Path,
    output: FakeOutput,
    clock: FakeClock,
    put_in_its_place: Callable[[App, CustomAnnouncement], None],
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    wait_for_speech(app)
    app.start()
    skip_everyone(app)  # only the custom one is read
    speak_ahead(app, [CLOSING])
    app.edit(row_of(app, Kind.CUSTOM), SEVEN_PM, CLOSING)
    app.skip(row_of(app, Kind.CUSTOM))

    put_in_its_place(app, CustomAnnouncement("Kapılar kapanıyor.", SEVEN_PM))
    wait_for_speech(app)

    assert [(change.kind, change.name) for change in app.changes] == [
        (Kind.START, AYSE),
        (Kind.END, AYSE),
        (Kind.START, MEHMET),
        (Kind.END, MEHMET),
    ]
    assert load_changes(app.changes_file, MONDAY) == app.changes
    custom = row_of(app, Kind.CUSTOM)
    assert (custom.text, custom.status, custom.edited) == (
        "Kapılar kapanıyor.",
        Status.NEXT,
        False,
    )
    clock.now = at(19)
    wait_for(app, lambda: status_of(app, Kind.CUSTOM) is Status.PLAYED)
    assert heard(output) == [CHIME, "Kapılar kapanıyor."]


def test_custom_announcement_given_other_days_keeps_todays_change(app: App) -> None:
    app.save_announcement(WATER)
    app.skip(row_of(app, Kind.CUSTOM))

    app.save_announcement(
        CustomAnnouncement(WATER.text, SEVEN_PM, frozenset({0, 2})), replacing=0
    )

    assert app.changes == (Change(MONDAY, at(19), Kind.CUSTOM, skipped=True),)
    assert statuses(app) == [Status.SKIPPING]


def test_instant_announcement_is_prepared_queued_read_and_shown_as_played(
    app: App, output: FakeOutput
) -> None:
    app.start()
    release = held_sounding(output, "Kapılar kapanıyor.")

    app.announce("  Kapılar kapanıyor.  ")

    assert instant(app, "Kapılar kapanıyor.").status is Status.PREPARING
    wait_for(app, lambda: instant(app, "Kapılar kapanıyor.").status is Status.PLAYING)
    app.announce("Salon temizleniyor.")
    wait_for(app, lambda: instant(app, "Salon temizleniyor.").status is Status.QUEUED)
    release.set()
    wait_for(app, lambda: instant(app, "Salon temizleniyor.").status is Status.PLAYED)

    assert instant(app, "Kapılar kapanıyor.").status is Status.PLAYED
    assert heard(output) == [
        CHIME,
        "Kapılar kapanıyor.",
        CHIME,
        "Salon temizleniyor.",
    ]
    assert app.last_announcement == LastAnnouncement(
        app.instants[1].asked_at,
        ((Kind.INSTANT, "Salon temizleniyor."),),
        Status.PLAYED,
    )


def test_instant_announcement_without_speech_is_shown_as_such(
    app: App, provider: FakeProvider, output: FakeOutput
) -> None:
    provider.offline = True
    app.start()

    app.announce("Kapılar kapanıyor.")
    wait_for(app, lambda: instant(app, "Kapılar kapanıyor.").status is Status.NO_SPEECH)

    assert output.played == []
    assert not app.sounding
    with pytest.raises(ValueError, match="no text"):
        app.announce("   ")
    assert len(app.instants) == 1


def test_instant_announcements_are_read_in_the_order_asked_for(
    app: App,
    provider: FakeProvider,
    output: FakeOutput,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synthesize = provider.synthesize

    def slow_at_first(text: str, voice: str, speed: float) -> bytes:
        if text == "Bir.":
            time.sleep(0.1)
        return synthesize(text, voice, speed)

    monkeypatch.setattr(provider, "synthesize", slow_at_first)
    app.start()

    for text in ("Bir.", "İki.", "Üç."):
        app.announce(text)
    wait_for(app, lambda: statuses(app) == [Status.PLAYED] * 3)

    assert heard(output) == [CHIME, "Bir.", CHIME, "İki.", CHIME, "Üç."]
    assert [row.text for row in app.rows()] == ["Bir.", "İki.", "Üç."]


def test_instant_announcement_is_read_again_on_request(
    app: App, provider: FakeProvider, output: FakeOutput
) -> None:
    app.start()
    app.announce("Kapılar kapanıyor.")
    wait_for(app, lambda: instant(app, "Kapılar kapanıyor.").status is Status.PLAYED)
    assert instant(app, "Kapılar kapanıyor.").replayable

    assert app.replay(instant(app, "Kapılar kapanıyor."))

    assert instant(app, "Kapılar kapanıyor.").status is Status.PREPARING
    assert not app.replay(instant(app, "Kapılar kapanıyor."))  # not while it is
    wait_for(app, lambda: instant(app, "Kapılar kapanıyor.").status is Status.PLAYED)
    assert heard(output) == [CHIME, "Kapılar kapanıyor."] * 2
    assert len(provider.calls) == 1  # its speech was kept
    assert len(app.instants) == 1


def test_same_instant_announcement_asked_for_twice_at_once_is_read_twice(
    app: App, output: FakeOutput
) -> None:
    app.start()

    app.announce("Kapılar kapanıyor.")
    app.announce("Kapılar kapanıyor.")

    wait_for(app, lambda: statuses(app) == [Status.PLAYED] * 2)
    first, second = app.instants
    assert first.asked_at < second.asked_at
    assert heard(output) == [CHIME, "Kapılar kapanıyor."] * 2


def test_only_todays_instant_announcements_are_listed(
    app: App, clock: FakeClock
) -> None:
    app.start()
    app.announce("Günaydın.")
    wait_for(app, lambda: instant(app, "Günaydın.").status is Status.PLAYED)

    clock.now = at(9) + DAY

    assert app.rows() == ()
    app.announce("İyi akşamlar.")
    assert [item.text for item in app.instants] == ["İyi akşamlar."]
    assert [row.text for row in app.rows()] == ["İyi akşamlar."]


def test_sounding_tells_whether_there_is_something_to_cut(
    app: App, output: FakeOutput
) -> None:
    app.start()
    assert not app.sounding
    release = held_sounding(output, "Kapılar kapanıyor.")

    app.announce("Kapılar kapanıyor.")

    assert app.sounding  # its speech is being made
    wait_for(app, lambda: instant(app, "Kapılar kapanıyor.").status is Status.PLAYING)
    assert app.sounding
    release.set()
    wait_for(app, lambda: instant(app, "Kapılar kapanıyor.").status is Status.PLAYED)
    assert not app.sounding


def test_cut_stops_what_sounds_and_what_is_queued_after_it_is_still_read(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    release = held_sounding(output, start_text(AYSE))
    clock.now = at(18)
    wait_for(app, lambda: status_of(app, Kind.START, AYSE) is Status.PLAYING)
    assert status_of(app, Kind.START, MEHMET) is Status.QUEUED
    app.announce("Kapılar kapanıyor.")
    wait_for(app, lambda: instant(app, "Kapılar kapanıyor.").status is Status.QUEUED)

    app.cut()
    app.cut()  # a second press must not cut off what comes next
    release.set()

    wait_for(app, lambda: instant(app, "Kapılar kapanıyor.").status is Status.PLAYED)
    assert statuses(app)[:2] == [Status.STOPPED] * 2
    assert heard(output) == [CHIME, start_text(AYSE), CHIME, "Kapılar kapanıyor."]
    assert row_of(app, Kind.START, AYSE).replayable
    assert not app.sounding


def test_athletes_of_a_minute_given_the_same_sentence_keep_their_own_outcomes(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    welcome = "Hoş geldiniz."
    speak_ahead(app, [welcome])
    for name in (AYSE, MEHMET):
        app.edit(row_of(app, Kind.START, name), SIX_PM, welcome)
    assert [row.text for row in app.rows()[:2]] == [welcome, welcome]
    release = held_sounding(output, welcome, nth=2)
    clock.now = at(18)
    wait_for(app, lambda: status_of(app, Kind.START, MEHMET) is Status.PLAYING)
    assert status_of(app, Kind.START, AYSE) is Status.PLAYED

    app.cut()
    release.set()

    wait_for(app, lambda: status_of(app, Kind.START, MEHMET) is Status.STOPPED)
    assert status_of(app, Kind.START, AYSE) is Status.PLAYED
    assert heard(output) == [CHIME, welcome, welcome]
    last = app.last_announcement
    assert last is not None
    assert last.status is Status.STOPPED


def test_cut_drops_what_is_still_being_prepared_to_be_read(
    app: App,
    provider: FakeProvider,
    output: FakeOutput,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def first_status() -> Status:
        return instant(app, "Kapılar kapanıyor.").status

    release = held_back(provider, monkeypatch, "Kapılar kapanıyor.")
    app.start()
    app.announce("Kapılar kapanıyor.")
    app.preview("Deneme anonsu.")
    assert (first_status(), app.test_status, app.sounding) == (
        Status.PREPARING,
        Status.PREPARING,
        True,
    )

    app.cut()

    assert (first_status(), app.test_status, app.sounding) == (
        Status.STOPPED,
        Status.STOPPED,
        False,
    )
    release.set()
    app.announce("Salon temizleniyor.")
    wait_for(app, lambda: instant(app, "Salon temizleniyor.").status is Status.PLAYED)
    assert heard(output) == [CHIME, "Salon temizleniyor."]
    assert (first_status(), app.test_status) == (Status.STOPPED, Status.STOPPED)


def test_cut_stops_a_test_announcement(app: App, output: FakeOutput) -> None:
    app.start()
    output.endless = True
    app.preview("Deneme anonsu.")
    wait_for(app, lambda: app.test_status is Status.PLAYING)
    assert app.sounding

    app.cut()
    output.endless = False

    wait_for(app, lambda: app.test_status is Status.STOPPED)
    assert heard(output) == [CHIME]
    assert not app.sounding
    assert app.last_announcement is None


def test_nothing_is_sounding_once_a_test_announcement_ends(
    app: App, output: FakeOutput
) -> None:
    app.start()
    output.endless = True
    app.test(DEFAULT_TEMPLATE)
    wait_for(app, lambda: app.test_status is Status.PLAYING)
    assert app.sounding

    output.endless = False

    wait_for(app, lambda: app.test_status is Status.PLAYED)
    assert not app.sounding
    assert heard(output) == [CHIME, start_text(TEST_NAME)]


def test_unreadable_changes_are_reported_and_the_plan_is_announced(
    make_app: Callable[[], App],
    schedule: Path,
    settings_file: Path,
    output: FakeOutput,
    clock: FakeClock,
) -> None:
    save_settings(Settings(schedule_path=schedule), settings_file)
    settings_file.with_name("changes.json").write_text("{ not json", encoding="utf-8")

    app = make_app()
    app.start()
    wait_for_speech(app)

    assert (app.changes_unreadable, app.changes) == (True, ())
    assert statuses(app) == [Status.NEXT] * 2 + [Status.WAITING] * 2
    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[:2] == [Status.PLAYED] * 2)
    assert len(output.played) == 3


def test_unreadable_changes_are_copied_and_the_first_change_replaces_them(
    make_app: Callable[[], App], schedule: Path, settings_file: Path
) -> None:
    save_settings(Settings(schedule_path=schedule), settings_file)
    changes_file = settings_file.with_name("changes.json")
    changes_file.write_text("{ not json", encoding="utf-8")

    app = make_app()
    app.start()

    assert (app.changes_unreadable, app.changes_backed_up) == (True, True)
    assert backup_copy(changes_file).read_text(encoding="utf-8") == "{ not json"
    app.skip(row_of(app, Kind.START, AYSE))
    assert not app.changes_unreadable
    assert load_changes(changes_file, MONDAY) == (
        Change(MONDAY, at(18), Kind.START, AYSE, skipped=True),
        Change(MONDAY, at(19), Kind.END, AYSE, skipped=True),
    )
    assert backup_copy(changes_file).read_text(encoding="utf-8") == "{ not json"
