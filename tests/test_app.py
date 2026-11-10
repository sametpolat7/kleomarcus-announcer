"""Tests for the app behind the window, with fake speech and a fake output."""

import datetime
import json
import logging
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from openpyxl import Workbook

from announcer.app import TEST_NAME, App, SlotTakenError, unreadable_copy
from announcer.chime import CHIME_PATH
from announcer.loader import RowProblem, ScheduleFileError, SkippedRow
from announcer.models import CustomAnnouncement
from announcer.settings import Settings, load_settings, save_settings
from announcer.template import DEFAULT_TEMPLATE
from announcer.timetable import SlotHolder
from announcer.today import Status
from fakes import FakeClock, FakeOutput, FakeProvider

MONDAY = datetime.date(2026, 11, 9)
SIX_PM, SEVEN_PM = datetime.time(18, 0), datetime.time(19, 0)
ROWS = [
    ("Ayşe Yılmaz", "Pazartesi", "18:00"),
    ("Mehmet Demir", "Pazartesi", "18:00"),
    ("Zeynep Kaya", "Salı", "20:00"),
]
WATER = CustomAnnouncement("Su içmeyi unutmayın.", SEVEN_PM)


def at(hour: int, minute: int = 0, second: int = 0) -> datetime.datetime:
    """Return a moment on the test Monday."""
    return datetime.datetime.combine(MONDAY, datetime.time(hour, minute, second))


def write_schedule(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    """Write a schedule file with the given name, day and time rows."""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(("Ad Soyad", "Gün", "Saat"))
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(at(8))


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def output() -> FakeOutput:
    return FakeOutput()


@pytest.fixture
def settings_file(tmp_path: Path) -> Path:
    return tmp_path / "settings" / "config.json"


@pytest.fixture
def schedule(tmp_path: Path) -> Path:
    return write_schedule(tmp_path / "schedule.xlsx", ROWS)


@pytest.fixture
def make_app(
    tmp_path: Path,
    settings_file: Path,
    provider: FakeProvider,
    output: FakeOutput,
    clock: FakeClock,
    no_waiting: None,
) -> Iterator[Callable[[], App]]:
    """Build apps over the fakes above; they are stopped after the test."""
    apps: list[App] = []

    def make() -> App:
        app = App(
            settings_file, tmp_path / "audio", provider, output, clock, interval=0.01
        )
        apps.append(app)
        return app

    yield make
    for app in apps:
        app.stop()


@pytest.fixture
def app(make_app: Callable[[], App]) -> App:
    return make_app()


def wait_for(app: App, condition: Callable[[], bool]) -> None:
    """Keep polling ``app`` until ``condition`` holds; fail if it never does."""
    deadline = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        app.poll()
        time.sleep(0.005)


def wait_for_speech(app: App) -> None:
    wait_for(app, lambda: not app.generating)


def statuses(app: App) -> list[Status]:
    return [row.status for row in app.rows()]


def test_fresh_app_has_the_defaults_and_nothing_to_announce(app: App) -> None:
    app.start()

    assert app.settings == Settings()
    assert app.rows() == ()
    assert app.refreshed_at is None
    assert app.schedule_error is None
    assert (app.settings_unreadable, app.dropped_announcements) == (False, 0)
    assert (app.generating, app.unspoken) == (False, ())
    assert (app.last_announcement, app.test_status) == (None, None)


def test_choosing_a_schedule_loads_it_and_remembers_the_file(
    app: App, schedule: Path, settings_file: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)

    assert [row.summary for row in app.rows()] == ["Ayşe Yılmaz, Mehmet Demir"]
    assert app.refreshed_at == clock.now
    assert app.settings.schedule_path == schedule
    assert load_settings(settings_file).settings.schedule_path == schedule


def test_choosing_a_schedule_generates_its_speech(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    assert app.generating
    wait_for_speech(app)

    assert app.unspoken == ()
    assert sorted(text for text, _ in provider.calls) == sorted(
        DEFAULT_TEMPLATE.replace("{ad}", name) for name, _, _ in ROWS
    )


def test_schedule_that_cannot_be_used_is_not_chosen(
    app: App, schedule: Path, tmp_path: Path
) -> None:
    app.choose_schedule(schedule)
    refreshed_at = app.refreshed_at
    broken = tmp_path / "broken.xlsx"
    broken.write_text("not a workbook")

    with pytest.raises(ScheduleFileError):
        app.choose_schedule(broken)

    assert app.settings.schedule_path == schedule
    assert len(app.rows()) == 1
    assert (app.schedule_error, app.refreshed_at) == (None, refreshed_at)


def test_start_loads_the_schedule_that_was_chosen_before(
    make_app: Callable[[], App], schedule: Path, settings_file: Path
) -> None:
    save_settings(Settings(schedule_path=schedule), settings_file)
    app = make_app()

    app.start()

    assert len(app.rows()) == 1
    assert app.refreshed_at is not None


def test_refresh_reads_the_file_again(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    write_schedule(schedule, [*ROWS, ("Elif Şahin", "Pazartesi", "20:30")])
    clock.now = at(9)

    app.refresh()

    assert [row.summary for row in app.rows()] == [
        "Ayşe Yılmaz, Mehmet Demir",
        "Elif Şahin",
    ]
    assert app.refreshed_at == at(9)


def test_refresh_generates_the_speech_that_is_missing(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    provider.offline = True
    app.choose_schedule(schedule)
    wait_for_speech(app)
    assert len(app.unspoken) == 3

    provider.offline = False
    app.refresh()
    wait_for_speech(app)

    assert not app.unspoken


def test_failed_refresh_keeps_the_schedule_that_was_read_last(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    schedule.write_text("no longer a workbook")
    clock.now = at(9)

    app.refresh()

    assert isinstance(app.schedule_error, ScheduleFileError)
    assert [row.summary for row in app.rows()] == ["Ayşe Yılmaz, Mehmet Demir"]
    assert app.refreshed_at == at(8)

    write_schedule(schedule, ROWS[:1])
    app.refresh()

    assert app.schedule_error is None
    assert [row.summary for row in app.rows()] == ["Ayşe Yılmaz"]


def test_skipped_rows_are_kept_for_the_window(app: App, tmp_path: Path) -> None:
    rows = [*ROWS, ("Can Aydın", "Pazartesi", "akşam"), ("", "Funday", "18:00")]

    app.choose_schedule(write_schedule(tmp_path / "typos.xlsx", rows))

    assert app.skipped_rows == (
        SkippedRow(5, (RowProblem.INVALID_TIME,)),
        SkippedRow(6, (RowProblem.MISSING_NAME, RowProblem.UNKNOWN_DAY)),
    )


def test_new_template_is_stored_and_spoken(
    app: App, schedule: Path, settings_file: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)

    app.set_template("Hoş geldiniz, {ad}.")
    wait_for_speech(app)

    assert load_settings(settings_file).settings.template == "Hoş geldiniz, {ad}."
    assert app.timetable.slots[0].texts[0] == "Hoş geldiniz, Ayşe Yılmaz."
    assert ("Hoş geldiniz, Zeynep Kaya.", app.settings.voice) in provider.calls
    assert app.unspoken == ()


def test_template_changed_while_speech_is_generated_is_spoken_too(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    assert app.generating
    app.set_template("Hoş geldiniz, {ad}.")
    wait_for_speech(app)

    assert ("Hoş geldiniz, Ayşe Yılmaz.", app.settings.voice) in provider.calls
    assert app.unspoken == ()


def test_template_without_the_placeholder_is_rejected(
    app: App, settings_file: Path
) -> None:
    with pytest.raises(ValueError, match="placeholder"):
        app.set_template("Hoş geldiniz.")

    assert app.settings.template == DEFAULT_TEMPLATE
    assert not settings_file.exists()


def test_custom_announcement_is_added_and_stored(
    app: App, schedule: Path, settings_file: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)

    app.save_announcement(WATER)

    assert load_settings(settings_file).settings.custom_announcements == (WATER,)
    assert [row.summary for row in app.rows()] == [
        "Ayşe Yılmaz, Mehmet Demir",
        "Su içmeyi unutmayın.",
    ]


def test_custom_announcement_cannot_share_a_slot_with_athletes(
    app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    wanted = CustomAnnouncement("Salon kapanıyor.", SIX_PM, frozenset({0, 3}))

    with pytest.raises(SlotTakenError) as raised:
        app.save_announcement(wanted)

    [conflict] = raised.value.conflicts
    assert (conflict.weekday, conflict.holder) == (0, SlotHolder.ATHLETES)
    assert app.settings.custom_announcements == ()


def test_custom_announcement_cannot_share_a_slot_with_another(app: App) -> None:
    app.save_announcement(WATER)
    wanted = CustomAnnouncement("Salon kapanıyor.", SEVEN_PM, frozenset({6}))

    with pytest.raises(SlotTakenError) as raised:
        app.save_announcement(wanted)

    [conflict] = raised.value.conflicts
    assert (conflict.weekday, conflict.holder) == (6, SlotHolder.CUSTOM_ANNOUNCEMENT)
    assert app.settings.custom_announcements == (WATER,)


def test_custom_announcement_is_changed_in_place(app: App) -> None:
    closing = CustomAnnouncement("Salon kapanıyor.", datetime.time(21, 45))
    app.save_announcement(WATER)
    app.save_announcement(closing)

    # Keeping its own time is no conflict with itself.
    changed = CustomAnnouncement("Bol su için.", SEVEN_PM, frozenset({0}))
    app.save_announcement(changed, replacing=0)

    assert app.settings.custom_announcements == (changed, closing)
    with pytest.raises(SlotTakenError):
        app.save_announcement(
            CustomAnnouncement("Bol su için.", closing.time), replacing=0
        )


def test_custom_announcement_is_deleted(app: App, settings_file: Path) -> None:
    closing = CustomAnnouncement("Salon kapanıyor.", datetime.time(21, 45))
    app.save_announcement(WATER)
    app.save_announcement(closing)

    app.delete_announcement(0)

    assert app.settings.custom_announcements == (closing,)
    assert load_settings(settings_file).settings.custom_announcements == (closing,)
    assert [row.summary for row in app.rows()] == ["Salon kapanıyor."]


def test_athletes_win_a_slot_when_a_reload_creates_a_conflict(
    app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    write_schedule(schedule, [*ROWS, ("Elif Şahin", "Pazartesi", "19:00")])

    app.refresh()

    assert [(row.summary, row.status) for row in app.rows()] == [
        ("Ayşe Yılmaz, Mehmet Demir", Status.NEXT),
        ("Elif Şahin", Status.WAITING),
        ("Su içmeyi unutmayın.", Status.CONFLICT),
    ]
    assert len(app.timetable.conflicts) == 1
    # The announcement stays stored: it still plays on the other days.
    assert app.settings.custom_announcements == (WATER,)


def test_change_that_cannot_be_saved_changes_nothing(
    app: App, settings_file: Path
) -> None:
    settings_file.mkdir(parents=True)  # a folder where the file should be

    with pytest.raises(OSError):
        app.set_template("Hoş geldiniz, {ad}.")
    with pytest.raises(OSError):
        app.save_announcement(WATER)

    assert app.settings == Settings()
    assert app.timetable.slots == ()


def test_due_slot_is_announced_and_shown_as_played(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock, tmp_path: Path
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    assert statuses(app) == [Status.NEXT]

    clock.now = at(18, 0, 1)
    app.start()
    wait_for(app, lambda: statuses(app) == [Status.PLAYED])

    assert output.played[0] == CHIME_PATH
    assert [path.parent for path in output.played[1:]] == [tmp_path / "audio"] * 2
    last = app.last_announcement
    assert last is not None
    assert (last.due, last.summary, last.status) == (
        at(18),
        "Ayşe Yılmaz, Mehmet Demir",
        Status.PLAYED,
    )


def test_slot_without_speech_is_shown_as_skipped(
    app: App,
    schedule: Path,
    provider: FakeProvider,
    output: FakeOutput,
    clock: FakeClock,
) -> None:
    provider.offline = True
    app.choose_schedule(schedule)
    wait_for_speech(app)
    assert len(app.unspoken) == 3

    clock.now = at(18)
    app.start()
    wait_for(app, lambda: statuses(app) == [Status.NO_SPEECH])

    assert output.played == []


def test_slot_with_speech_for_some_is_shown_as_partly_played(
    app: App, schedule: Path, provider: FakeProvider, clock: FakeClock
) -> None:
    provider.unspeakable = {DEFAULT_TEMPLATE.replace("{ad}", "Mehmet Demir")}
    app.choose_schedule(schedule)
    wait_for_speech(app)
    assert app.unspoken == tuple(provider.unspeakable)

    clock.now = at(18)
    app.start()
    wait_for(app, lambda: statuses(app) == [Status.PARTLY_PLAYED])


def test_slot_that_the_output_cannot_play_is_shown_as_failed(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    output.no_device = True
    app.choose_schedule(schedule)
    wait_for_speech(app)

    clock.now = at(18)
    app.start()
    wait_for(app, lambda: statuses(app) == [Status.NO_OUTPUT])

    last = app.last_announcement
    assert last is not None
    assert last.status is Status.NO_OUTPUT


def test_slot_that_was_slept_through_is_shown_as_skipped(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(CustomAnnouncement("Günaydın.", datetime.time(8, 0)))
    app.save_announcement(WATER)
    wait_for_speech(app)

    app.start()
    wait_for(app, lambda: statuses(app)[0] is Status.PLAYED)
    played = list(output.played)
    clock.now = at(18, 30)  # the computer slept through 18:00
    wait_for(app, lambda: statuses(app)[1] is Status.ASLEEP)

    assert statuses(app) == [Status.PLAYED, Status.ASLEEP, Status.NEXT]
    assert output.played == played
    last = app.last_announcement
    assert last is not None
    assert last.summary == "Günaydın."


def test_outcomes_of_yesterday_are_not_shown_today(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    wait_for_speech(app)
    clock.now = at(19)
    app.start()
    wait_for(app, lambda: statuses(app)[-1] is Status.PLAYED)

    clock.now = at(19) + datetime.timedelta(days=7)
    wait_for(app, lambda: statuses(app)[-1] is Status.PLAYED)

    assert statuses(app) == [Status.ASLEEP, Status.PLAYED]


def test_test_announcement_speaks_the_template_with_a_made_up_name(
    app: App, provider: FakeProvider, output: FakeOutput
) -> None:
    app.start()

    app.test("Merhaba {ad}.")
    assert app.test_status is Status.PLAYING
    wait_for(app, lambda: app.test_status is Status.PLAYED)

    assert provider.calls == [(f"Merhaba {TEST_NAME}.", app.settings.voice)]
    assert output.played[0] == CHIME_PATH
    assert len(output.played) == 2
    # Trying a template out neither stores it nor counts as an announcement.
    assert app.settings.template == DEFAULT_TEMPLATE
    assert app.last_announcement is None


def test_test_announcement_without_speech_is_reported(
    app: App, provider: FakeProvider, output: FakeOutput
) -> None:
    provider.offline = True
    app.start()

    app.test(DEFAULT_TEMPLATE)
    wait_for(app, lambda: app.test_status is Status.NO_SPEECH)

    assert output.played == []


def test_test_announcement_that_cannot_be_played_is_reported(
    app: App, output: FakeOutput
) -> None:
    output.no_device = True
    app.start()

    app.test(DEFAULT_TEMPLATE)
    wait_for(app, lambda: app.test_status is Status.NO_OUTPUT)


def test_test_announcement_needs_the_placeholder(app: App) -> None:
    with pytest.raises(ValueError, match="placeholder"):
        app.test("Merhaba.")

    assert app.test_status is None


def test_dropped_custom_announcements_are_counted(
    make_app: Callable[[], App], settings_file: Path
) -> None:
    settings_file.parent.mkdir(parents=True)
    stored = {
        "custom_announcements": [
            {"text": "Duyuru", "time": "25:00", "weekdays": [0]},
            {"text": "Su içmeyi unutmayın.", "time": "19:00", "weekdays": [0]},
        ]
    }
    settings_file.write_text(json.dumps(stored), encoding="utf-8")

    app = make_app()

    assert app.dropped_announcements == 1
    assert len(app.settings.custom_announcements) == 1


def test_unreadable_settings_are_set_aside_and_the_defaults_used(
    make_app: Callable[[], App], settings_file: Path
) -> None:
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text("{ not json", encoding="utf-8")

    app = make_app()
    app.save_announcement(WATER)

    assert app.settings_unreadable
    assert unreadable_copy(settings_file).read_text(encoding="utf-8") == "{ not json"
    assert load_settings(settings_file).settings.custom_announcements == (WATER,)


def test_actions_and_outcomes_are_logged_with_their_cause(
    app: App,
    schedule: Path,
    provider: FakeProvider,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="announcer")
    provider.unspeakable = {DEFAULT_TEMPLATE.replace("{ad}", "Mehmet Demir")}
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    wait_for_speech(app)
    clock.now = at(18)
    app.start()
    wait_for(app, lambda: statuses(app)[0] is Status.PARTLY_PLAYED)
    clock.now = at(20)
    wait_for(app, lambda: statuses(app)[1] is Status.ASLEEP)

    log = caplog.text
    assert "Schedule loaded from" in log
    assert "3 session(s), 0 row(s) skipped" in log
    assert 'Custom announcement added: "Su içmeyi unutmayın." at 19:00 on' in log
    assert 'No speech for "Mehmet Demir, antrenmanınız başlamıştır.' in log
    assert '18:00 announced: "Ayşe Yılmaz, antrenmanınız başlamıştır.' in log
    assert '18:00 skipped, no stored speech: "Mehmet Demir, antrenmanınız' in log
    assert "2026-11-09 19:00 skipped, the computer was asleep" in log
