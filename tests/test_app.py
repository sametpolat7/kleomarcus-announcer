import datetime
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from announcer import VERSION
from announcer.app import TEST_NAME, App, backup_copy
from announcer.chime import CHIME_PATH
from announcer.loader import ScheduleFileError, UnreadableFileError
from announcer.models import CustomAnnouncement, Kind
from announcer.settings import Settings, load_settings, save_settings
from announcer.template import DEFAULT_END_TEMPLATE, DEFAULT_TEMPLATE, render
from announcer.today import Row, Status
from support import (
    MONDAY,
    ROWS,
    FakeClock,
    FakeOutput,
    FakeProvider,
    at,
    statuses,
    wait_for,
    wait_for_speech,
    write_schedule,
)

SIX_PM, FIVE_TO_SEVEN = datetime.time(18, 0), datetime.time(18, 55)
SEVEN_PM = datetime.time(19, 0)
WATER = CustomAnnouncement("Su içmeyi unutmayın.", FIVE_TO_SEVEN)
WELCOME, GOODBYE = "Hoş geldiniz, {ad_soyad}.", "Güle güle, {ad_soyad}."
# The statuses of today's rows of the schedule: the start of both sessions
# at six and their end at five to seven, before six and once six is read.
UPCOMING = [Status.NEXT, Status.NEXT, Status.WAITING, Status.WAITING]
READ_AT_SIX = [Status.PLAYED, Status.PLAYED, Status.NEXT, Status.NEXT]


def whom(row: Row) -> str:
    """The athlete that a row is for, or what a custom announcement says."""
    return row.key[2] if isinstance(row.key, tuple) else row.text


def starts(app: App) -> list[str]:
    """Each athlete welcomed today is also seen off 55 minutes later."""
    return [whom(row) for row in app.rows() if row.kind is Kind.START]


def started(name: str) -> str:
    return render(DEFAULT_TEMPLATE, name)


def ended(name: str) -> str:
    return render(DEFAULT_END_TEMPLATE, name)


def stored_speech(tmp_path: Path) -> set[Path]:
    return set((tmp_path / "audio").iterdir())


def test_choosing_a_schedule_loads_it_remembers_the_file_and_generates_its_speech(
    app: App,
    schedule: Path,
    settings_file: Path,
    clock: FakeClock,
    provider: FakeProvider,
) -> None:
    app.choose_schedule(schedule)

    assert [(row.time, row.kind, row.text) for row in app.rows()] == [
        (SIX_PM, Kind.START, started("Ayşe Yılmaz")),
        (SIX_PM, Kind.START, started("Mehmet Demir")),
        (FIVE_TO_SEVEN, Kind.END, ended("Ayşe Yılmaz")),
        (FIVE_TO_SEVEN, Kind.END, ended("Mehmet Demir")),
    ]
    assert app.refreshed_at == clock.now
    assert load_settings(settings_file).settings.schedule_path == schedule
    assert app.generating
    wait_for_speech(app)

    assert app.unspoken == ()
    assert sorted(text for text, _ in provider.calls) == sorted(
        render(template, name)
        for template in (DEFAULT_TEMPLATE, DEFAULT_END_TEMPLATE)
        for name, _, _ in ROWS
    )


def test_athletes_of_a_minute_have_a_row_each_in_the_order_of_the_schedule(
    app: App, tmp_path: Path
) -> None:
    in_order = [
        ("Zeynep Kaya", "Pazartesi", "18:00"),
        ("Can Öztürk", "Pazartesi", "18:55"),
        ("Ayşe Yılmaz", "Pazartesi", "18:00"),
        ("Mehmet Demir", "Pazartesi", "18:00"),
    ]
    app.choose_schedule(write_schedule(tmp_path / "in-order.xlsx", in_order))
    ten_to_eight = datetime.time(19, 50)
    app.save_announcement(CustomAnnouncement("Su içmeyi unutmayın.", ten_to_eight))

    assert [(row.time, row.kind, whom(row)) for row in app.rows()] == [
        (SIX_PM, Kind.START, "Zeynep Kaya"),
        (SIX_PM, Kind.START, "Ayşe Yılmaz"),
        (SIX_PM, Kind.START, "Mehmet Demir"),
        (FIVE_TO_SEVEN, Kind.END, "Zeynep Kaya"),
        (FIVE_TO_SEVEN, Kind.END, "Ayşe Yılmaz"),
        (FIVE_TO_SEVEN, Kind.END, "Mehmet Demir"),
        (FIVE_TO_SEVEN, Kind.START, "Can Öztürk"),
        (ten_to_eight, Kind.END, "Can Öztürk"),
        (ten_to_eight, Kind.CUSTOM, "Su içmeyi unutmayın."),
    ]
    slot = app.timetable.slot_at(MONDAY, FIVE_TO_SEVEN)
    assert slot is not None
    assert slot.texts == (
        ended("Zeynep Kaya"),
        ended("Ayşe Yılmaz"),
        ended("Mehmet Demir"),
        started("Can Öztürk"),
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
    assert starts(app) == ["Ayşe Yılmaz", "Mehmet Demir"]
    assert (app.schedule_error, app.refreshed_at) == (None, refreshed_at)


def test_start_loads_the_schedule_that_was_chosen_before(
    make_app: Callable[[], App], schedule: Path, settings_file: Path
) -> None:
    save_settings(Settings(schedule_path=schedule), settings_file)
    app = make_app()

    app.start()

    assert starts(app) == ["Ayşe Yılmaz", "Mehmet Demir"]
    assert app.refreshed_at is not None


def test_refresh_reads_the_file_again(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    write_schedule(schedule, [*ROWS, ("Elif Şahin", "Pazartesi", "20:30")])
    clock.now = at(9)

    app.refresh()

    assert starts(app) == ["Ayşe Yılmaz", "Mehmet Demir", "Elif Şahin"]
    assert app.refreshed_at == at(9)


def test_failed_refresh_keeps_the_schedule_that_was_read_last(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    schedule.write_text("no longer a workbook")
    clock.now = at(9)

    app.refresh()

    assert isinstance(app.schedule_error, ScheduleFileError)
    assert starts(app) == ["Ayşe Yılmaz", "Mehmet Demir"]
    assert app.refreshed_at == at(8)

    write_schedule(schedule, ROWS[:1])
    app.refresh()

    assert app.schedule_error is None
    assert starts(app) == ["Ayşe Yılmaz"]


def test_schedule_file_that_was_saved_is_read_again_by_itself(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.start()
    clock.now = at(9)

    write_schedule(schedule, [*ROWS, ("Elif Şahin", "Pazartesi", "20:30")])
    wait_for(app, lambda: len(starts(app)) == 3)

    assert starts(app) == ["Ayşe Yılmaz", "Mehmet Demir", "Elif Şahin"]
    assert app.refreshed_at == at(9)


def test_schedule_file_is_not_read_again_while_it_is_unchanged(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.start()
    wait_for_speech(app)
    clock.now = at(9)

    deadline = time.monotonic() + 0.3  # a good dozen looks at the file
    while time.monotonic() < deadline:
        assert not app.poll()
        time.sleep(0.01)

    assert app.refreshed_at == at(8)


def test_schedule_file_that_is_still_being_written_is_left_alone(
    app: App, schedule: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("announcer.app._WATCH", 0.2)
    app.choose_schedule(schedule)
    app.start()

    for size in range(1, 60):
        schedule.write_bytes(b"x" * size)
        app.poll()
        time.sleep(0.01)

    assert app.schedule_error is None
    assert starts(app) == ["Ayşe Yılmaz", "Mehmet Demir"]


def test_schedule_file_that_disappears_is_reported_and_read_when_it_returns(
    app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    app.start()

    schedule.unlink()
    wait_for(app, lambda: app.schedule_error is not None)
    assert starts(app) == ["Ayşe Yılmaz", "Mehmet Demir"]

    write_schedule(schedule, ROWS[:1])
    wait_for(app, lambda: app.schedule_error is None)
    assert starts(app) == ["Ayşe Yılmaz"]


def test_changed_schedule_file_that_cannot_be_read_is_tried_a_few_times(
    app: App, schedule: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app.choose_schedule(schedule)
    app.start()

    schedule.write_text("not a workbook, and it stays that way")
    with caplog.at_level(logging.ERROR, logger="announcer.app"):
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            app.poll()
            time.sleep(0.01)

    # Once for the change and three more times, then no more until it changes.
    assert caplog.text.count("Cannot load the schedule") == 4
    assert app.schedule_error is not None


def test_missing_speech_is_tried_again_every_few_minutes(
    app: App, schedule: Path, provider: FakeProvider, clock: FakeClock
) -> None:
    provider.offline = True
    app.choose_schedule(schedule)
    wait_for_speech(app)
    asked = len(provider.calls)

    clock.now += datetime.timedelta(minutes=4)
    assert not app.poll()
    assert len(provider.calls) == asked

    provider.offline = False
    clock.now += datetime.timedelta(minutes=1)
    assert app.poll()  # the window is told, so it can show that speech is made
    wait_for_speech(app)

    assert not app.unspoken
    assert len(provider.calls) == asked + 6  # each athlete's start and end


def test_schedule_file_that_cannot_be_read_is_tried_again_every_few_minutes(
    app: App, schedule: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def held(path: Path) -> None:
        raise UnreadableFileError("held by another program for a moment")

    app.choose_schedule(schedule)
    wait_for_speech(app)
    with monkeypatch.context() as patch:
        patch.setattr("announcer.app.load_schedule", held)
        app.refresh()
    assert app.schedule_error is not None

    # The file looks no different once it can be read, so no change is seen.
    clock.now += datetime.timedelta(minutes=4)
    assert not app.poll()
    clock.now += datetime.timedelta(minutes=1)
    assert app.poll()

    assert app.schedule_error is None
    assert app.refreshed_at == clock.now


def test_slot_that_is_running_when_the_app_starts_is_not_announced(
    make_app: Callable[[], App],
    schedule: Path,
    output: FakeOutput,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    before = make_app()
    before.choose_schedule(schedule)
    wait_for_speech(before)
    before.stop()
    clock.now = at(18, 0, 5)
    # It reads the schedule as it starts, from the settings that ``before`` left.
    app = make_app()

    with caplog.at_level(logging.WARNING, logger="announcer.app"):
        app.start()

    assert statuses(app) == [Status.PASSED, Status.PASSED, Status.NEXT, Status.NEXT]
    assert "18:00 not announced, its minute had begun when the app started" in (
        caplog.text
    )
    clock.now = at(18, 0, 6)
    time.sleep(0.1)
    app.poll()
    assert output.played == []


def test_template_changed_while_speech_is_generated_is_spoken_too(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    assert app.generating
    app.set_templates(WELCOME, DEFAULT_END_TEMPLATE)
    wait_for_speech(app)

    assert ("Hoş geldiniz, Ayşe Yılmaz.", app.settings.voice) in provider.calls
    assert app.unspoken == ()


def test_template_without_the_placeholder_is_rejected(
    app: App, settings_file: Path
) -> None:
    with pytest.raises(ValueError, match="placeholder"):
        app.set_templates("Hoş geldiniz.", DEFAULT_END_TEMPLATE)
    with pytest.raises(ValueError, match="placeholder"):
        app.set_templates(DEFAULT_TEMPLATE, "Güle güle.")

    assert app.settings == Settings()
    assert not settings_file.exists()


def test_custom_announcement_is_added_and_stored(
    app: App, schedule: Path, settings_file: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)

    app.save_announcement(WATER)

    assert load_settings(settings_file).settings.custom_announcements == (WATER,)
    # It follows the end of a session in the minute it shares with that.
    assert [(row.time, row.kind, row.text) for row in app.rows()] == [
        (SIX_PM, Kind.START, started("Ayşe Yılmaz")),
        (SIX_PM, Kind.START, started("Mehmet Demir")),
        (FIVE_TO_SEVEN, Kind.END, ended("Ayşe Yılmaz")),
        (FIVE_TO_SEVEN, Kind.END, ended("Mehmet Demir")),
        (FIVE_TO_SEVEN, Kind.CUSTOM, "Su içmeyi unutmayın."),
    ]


def test_custom_announcement_shares_its_minute_with_a_start_and_with_another_one(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    welcome = CustomAnnouncement("Hoş geldiniz.", SIX_PM, frozenset({0, 3}))
    closing = CustomAnnouncement("Salon kapanıyor.", FIVE_TO_SEVEN)

    app.save_announcement(welcome)
    app.save_announcement(closing)

    assert app.settings.custom_announcements == (WATER, welcome, closing)
    assert [(row.time, row.kind, whom(row)) for row in app.rows()] == [
        (SIX_PM, Kind.START, "Ayşe Yılmaz"),
        (SIX_PM, Kind.START, "Mehmet Demir"),
        (SIX_PM, Kind.CUSTOM, "Hoş geldiniz."),
        (FIVE_TO_SEVEN, Kind.END, "Ayşe Yılmaz"),
        (FIVE_TO_SEVEN, Kind.END, "Mehmet Demir"),
        (FIVE_TO_SEVEN, Kind.CUSTOM, "Su içmeyi unutmayın."),
        (FIVE_TO_SEVEN, Kind.CUSTOM, "Salon kapanıyor."),
    ]
    wait_for_speech(app)
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[:3] == [Status.PLAYED] * 3)

    last = app.last_announcement
    assert last is not None
    assert (last.due, last.parts) == (
        at(18),
        (
            (Kind.START, "Ayşe Yılmaz"),
            (Kind.START, "Mehmet Demir"),
            (Kind.CUSTOM, "Hoş geldiniz."),
        ),
    )
    assert len(output.played) == 4


def test_custom_announcement_is_changed_in_place(app: App) -> None:
    closing = CustomAnnouncement("Salon kapanıyor.", datetime.time(21, 45))
    app.save_announcement(WATER)
    app.save_announcement(closing)

    changed = CustomAnnouncement("Bol su için.", FIVE_TO_SEVEN, frozenset({0}))
    app.save_announcement(changed, replacing=0)

    assert app.settings.custom_announcements == (changed, closing)

    moved = CustomAnnouncement("Bol su için.", closing.time)
    app.save_announcement(moved, replacing=0)

    assert app.settings.custom_announcements == (moved, closing)
    assert [(row.time, row.text) for row in app.rows()] == [
        (closing.time, "Bol su için."),
        (closing.time, "Salon kapanıyor."),
    ]


def test_custom_announcement_is_deleted(app: App, settings_file: Path) -> None:
    closing = CustomAnnouncement("Salon kapanıyor.", datetime.time(21, 45))
    app.save_announcement(WATER)
    app.save_announcement(closing)

    app.delete_announcement(0)

    assert app.settings.custom_announcements == (closing,)
    assert load_settings(settings_file).settings.custom_announcements == (closing,)
    assert [row.text for row in app.rows()] == ["Salon kapanıyor."]


def test_custom_announcement_is_read_after_a_start_that_a_reload_puts_in_its_minute(
    app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    write_schedule(schedule, [*ROWS, ("Elif Şahin", "Pazartesi", "18:55")])

    app.refresh()

    assert [(row.kind, whom(row), row.status) for row in app.rows()] == [
        (Kind.START, "Ayşe Yılmaz", Status.NEXT),
        (Kind.START, "Mehmet Demir", Status.NEXT),
        (Kind.END, "Ayşe Yılmaz", Status.WAITING),
        (Kind.END, "Mehmet Demir", Status.WAITING),
        (Kind.START, "Elif Şahin", Status.WAITING),
        (Kind.CUSTOM, "Su içmeyi unutmayın.", Status.WAITING),
        (Kind.END, "Elif Şahin", Status.WAITING),
    ]
    slot = app.timetable.slot_at(MONDAY, FIVE_TO_SEVEN)
    assert slot is not None
    assert slot.texts == (
        ended("Ayşe Yılmaz"),
        ended("Mehmet Demir"),
        started("Elif Şahin"),
        "Su içmeyi unutmayın.",
    )
    assert app.settings.custom_announcements == (WATER,)


def test_change_that_cannot_be_saved_changes_nothing(
    app: App, settings_file: Path
) -> None:
    settings_file.mkdir(parents=True)  # a folder where the file should be

    with pytest.raises(OSError):
        app.set_templates(WELCOME, GOODBYE)
    with pytest.raises(OSError):
        app.set_end_announcements(False)
    with pytest.raises(OSError):
        app.save_announcement(WATER)
    with pytest.raises(OSError):
        app.set_speed(0.9)
    with pytest.raises(OSError):
        app.set_start_delay(2)
    with pytest.raises(OSError):
        app.set_end_delay(2)

    assert app.settings == Settings()
    assert app.timetable.slots == ()
    assert not app.generating


def test_speed_and_delay_that_are_not_offered_are_rejected(app: App) -> None:
    with pytest.raises(ValueError, match="speed"):
        app.set_speed(2.0)
    with pytest.raises(ValueError, match="delay"):
        app.set_start_delay(4)
    with pytest.raises(ValueError, match="delay"):
        app.set_end_delay(4)

    assert app.settings == Settings()


def test_due_slot_is_announced_and_shown_as_played(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock, tmp_path: Path
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    assert statuses(app) == UPCOMING

    app.start()
    clock.now = at(18, 0, 1)
    wait_for(app, lambda: statuses(app) == READ_AT_SIX)

    assert output.played[0] == CHIME_PATH
    assert [path.parent for path in output.played[1:]] == [tmp_path / "audio"] * 2
    last = app.last_announcement
    assert last is not None
    assert (last.due, last.parts, last.status) == (
        at(18),
        ((Kind.START, "Ayşe Yılmaz"), (Kind.START, "Mehmet Demir")),
        Status.PLAYED,
    )

    clock.now = at(18, 55, 1)
    wait_for(app, lambda: statuses(app) == [Status.PLAYED] * 4)

    assert len(output.played) == 6
    last = app.last_announcement
    assert last is not None
    assert (last.due, last.parts) == (
        at(18, 55),
        ((Kind.END, "Ayşe Yılmaz"), (Kind.END, "Mehmet Demir")),
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
    assert len(app.unspoken) == 6

    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[:3] == [*[Status.NO_SPEECH] * 2, Status.NEXT])

    assert output.played == []


def test_slot_that_the_output_cannot_play_is_shown_as_failed(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    output.no_device = True
    app.choose_schedule(schedule)
    wait_for_speech(app)

    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[:3] == [*[Status.NO_OUTPUT] * 2, Status.NEXT])

    last = app.last_announcement
    assert last is not None
    assert last.status is Status.NO_OUTPUT


def test_slot_that_was_slept_through_is_shown_as_skipped(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(CustomAnnouncement("Günaydın.", datetime.time(9, 0)))
    app.save_announcement(WATER)
    wait_for_speech(app)

    app.start()
    clock.now = at(9)
    wait_for(app, lambda: statuses(app)[0] is Status.PLAYED)
    played = list(output.played)
    clock.now = at(18, 30)
    wait_for(app, lambda: statuses(app)[1] is Status.ASLEEP)

    assert statuses(app) == [
        Status.PLAYED,
        Status.ASLEEP,
        Status.ASLEEP,
        Status.NEXT,
        Status.NEXT,
        Status.NEXT,
    ]
    assert output.played == played
    last = app.last_announcement
    assert last is not None
    assert last.parts == ((Kind.CUSTOM, "Günaydın."),)


def test_outcomes_of_yesterday_are_not_shown_today(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    wait_for_speech(app)
    app.start()
    clock.now = at(18, 55)
    wait_for(app, lambda: statuses(app)[-1] is Status.PLAYED)

    clock.now = at(18, 55) + datetime.timedelta(days=7)
    wait_for(app, lambda: statuses(app)[-1] is Status.PLAYED)

    assert statuses(app) == [*[Status.ASLEEP] * 2, *[Status.PLAYED] * 3]


def test_test_announcement_speaks_the_template_with_a_made_up_name(
    app: App, provider: FakeProvider, output: FakeOutput
) -> None:
    app.start()
    with pytest.raises(ValueError, match="placeholder"):
        app.test("Merhaba.")
    assert app.test_status is None

    app.test("Merhaba {ad_soyad}.")
    assert app.test_status is Status.PREPARING
    wait_for(app, lambda: app.test_status is Status.PLAYED)

    assert provider.calls == [(f"Merhaba {TEST_NAME}.", app.settings.voice)]
    assert output.played[0] == CHIME_PATH
    assert len(output.played) == 2
    assert app.settings.template == DEFAULT_TEMPLATE
    assert app.last_announcement is None


def test_any_text_can_be_tried_out(
    app: App, provider: FakeProvider, output: FakeOutput
) -> None:
    app.start()

    app.preview("Lütfen salonu temiz tutunuz.")
    wait_for(app, lambda: app.test_status is Status.PLAYED)

    assert provider.calls == [("Lütfen salonu temiz tutunuz.", app.settings.voice)]
    assert len(output.played) == 2
    assert app.settings.custom_announcements == ()


def test_test_announcement_without_speech_is_reported(
    app: App, provider: FakeProvider, output: FakeOutput
) -> None:
    provider.offline = True
    app.start()

    app.test(DEFAULT_TEMPLATE)
    wait_for(app, lambda: app.test_status is Status.NO_SPEECH)

    assert output.played == []


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
    assert app.settings_backed_up
    app.set_templates(WELCOME, DEFAULT_END_TEMPLATE)
    assert '"25:00"' not in settings_file.read_text(encoding="utf-8")
    assert '"25:00"' in backup_copy(settings_file).read_text(encoding="utf-8")


def test_unreadable_settings_are_copied_and_the_defaults_used(
    make_app: Callable[[], App], settings_file: Path
) -> None:
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text("{ not json", encoding="utf-8")

    app = make_app()
    app.save_announcement(WATER)

    assert (app.settings_unreadable, app.settings_backed_up) == (True, True)
    assert backup_copy(settings_file).read_text(encoding="utf-8") == "{ not json"
    assert load_settings(settings_file).settings.custom_announcements == (WATER,)


def test_settings_without_problems_are_not_copied(
    make_app: Callable[[], App], settings_file: Path
) -> None:
    save_settings(Settings(custom_announcements=(WATER,)), settings_file)

    app = make_app()

    assert not app.settings_backed_up
    assert not backup_copy(settings_file).exists()


def test_athletes_keep_the_old_template_until_the_new_one_has_speech(
    app: App,
    schedule: Path,
    provider: FakeProvider,
    output: FakeOutput,
    clock: FakeClock,
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    old = app.timetable.slots[0].texts, app.timetable.slots[1].texts

    provider.offline = True
    app.set_templates(WELCOME, GOODBYE)
    wait_for_speech(app)

    assert (app.settings.template, app.settings.end_template) == (WELCOME, GOODBYE)
    assert (app.spoken_template, app.spoken_end_template) == (
        DEFAULT_TEMPLATE,
        DEFAULT_END_TEMPLATE,
    )
    assert (app.timetable.slots[0].texts, app.timetable.slots[1].texts) == old
    assert len(app.unspoken) == 6
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app) == READ_AT_SIX)
    assert len(output.played) == 3  # the chime and both athletes

    provider.offline = False
    app.refresh()
    wait_for_speech(app)

    assert (app.spoken_template, app.spoken_end_template) == (WELCOME, GOODBYE)
    assert app.timetable.slots[0].texts[0] == "Hoş geldiniz, Ayşe Yılmaz."
    assert app.timetable.slots[1].texts[0] == "Güle güle, Ayşe Yılmaz."
    assert not app.unspoken


def test_outcome_stays_with_its_athlete_when_the_template_changes(
    app: App, schedule: Path, provider: FakeProvider, clock: FakeClock
) -> None:
    provider.unspeakable = {started("Mehmet Demir")}
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[:2] == [Status.PLAYED, Status.NO_SPEECH])
    clock.now = at(18, 5)

    app.set_templates(WELCOME, GOODBYE)
    wait_for(app, lambda: app.spoken_template == WELCOME)

    assert [(row.text, row.status) for row in app.rows()] == [
        ("Hoş geldiniz, Ayşe Yılmaz.", Status.PLAYED),
        ("Hoş geldiniz, Mehmet Demir.", Status.NO_SPEECH),
        ("Güle güle, Ayşe Yılmaz.", Status.NEXT),
        ("Güle güle, Mehmet Demir.", Status.NEXT),
    ]


def test_template_with_stored_speech_is_spoken_at_once(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.set_templates(WELCOME, GOODBYE)
    wait_for_speech(app)
    provider.offline = True

    app.set_templates(DEFAULT_TEMPLATE, DEFAULT_END_TEMPLATE)

    assert not app.generating
    assert (app.spoken_template, app.spoken_end_template) == (
        DEFAULT_TEMPLATE,
        DEFAULT_END_TEMPLATE,
    )


def test_new_speed_is_stored_and_spoken_once_its_speech_exists(
    app: App,
    schedule: Path,
    settings_file: Path,
    provider: FakeProvider,
    output: FakeOutput,
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    normal = stored_speech(tmp_path)

    app.set_speed(0.9)

    assert load_settings(settings_file).settings.speed == 0.9
    assert (app.generating, app.spoken_speed) == (True, 1.0)
    wait_for_speech(app)
    assert app.spoken_speed == 0.9
    assert provider.speeds == [1.0] * 6 + [0.9] * 6
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app) == READ_AT_SIX)
    assert len(output.played) == 3
    assert not normal.intersection(output.played)


def test_announcements_keep_the_old_speed_until_the_new_one_has_speech(
    app: App,
    schedule: Path,
    provider: FakeProvider,
    output: FakeOutput,
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    wait_for_speech(app)
    normal = stored_speech(tmp_path)

    provider.offline = True
    app.set_speed(1.2)
    wait_for_speech(app)

    assert (app.settings.speed, app.spoken_speed) == (1.2, 1.0)
    assert len(app.unspoken) == 7
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[0] is Status.PLAYED)
    assert normal.issuperset(output.played[1:])

    provider.offline = False
    app.refresh()
    wait_for_speech(app)

    assert app.spoken_speed == 1.2
    assert not app.unspoken
    clock.now = at(18, 55)
    wait_for(app, lambda: statuses(app)[-1] is Status.PLAYED)
    assert output.played[-1] not in normal  # a custom announcement has it too


def test_speed_with_stored_speech_is_spoken_at_once(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.set_speed(0.8)
    wait_for_speech(app)
    provider.offline = True

    app.set_speed(1.0)

    assert not app.generating
    assert app.spoken_speed == 1.0


def test_test_announcement_is_spoken_at_the_speed_in_the_settings(
    app: App, schedule: Path, provider: FakeProvider, output: FakeOutput
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    provider.unspeakable = {DEFAULT_TEMPLATE.replace("{ad_soyad}", "Zeynep Kaya")}
    app.set_speed(0.8)
    wait_for_speech(app)
    assert app.spoken_speed == 1.0  # one athlete has no speech at the new speed
    app.start()

    app.preview("Deneme anonsu.")
    wait_for(app, lambda: app.test_status is Status.PLAYED)

    assert provider.speeds[-1] == 0.8
    assert len(output.played) == 2


def test_delays_are_stored_and_move_the_starts_and_the_ends_but_no_custom_announcement(
    app: App,
    schedule: Path,
    settings_file: Path,
    output: FakeOutput,
    clock: FakeClock,
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    wait_for_speech(app)

    app.set_start_delay(2)
    app.set_end_delay(5)

    stored = load_settings(settings_file).settings
    assert (stored.start_delay, stored.end_delay) == (2, 5)
    assert not app.generating  # the speech is the same
    assert [(row.time, row.kind) for row in app.rows()] == [
        (datetime.time(18, 2), Kind.START),
        (datetime.time(18, 2), Kind.START),
        (FIVE_TO_SEVEN, Kind.CUSTOM),
        (SEVEN_PM, Kind.END),
        (SEVEN_PM, Kind.END),
    ]
    app.start()
    clock.now = at(18)
    time.sleep(0.1)
    app.poll()
    assert (statuses(app), output.played) == (
        [*[Status.NEXT] * 2, *[Status.WAITING] * 3],
        [],
    )

    clock.now = at(18, 2)
    wait_for(app, lambda: statuses(app)[1] is Status.PLAYED)
    clock.now = at(18, 55)
    wait_for(app, lambda: statuses(app)[2] is Status.PLAYED)
    assert statuses(app) == [*[Status.PLAYED] * 3, *[Status.NEXT] * 2]
    clock.now = at(19)
    wait_for(app, lambda: statuses(app)[-1] is Status.PLAYED)

    last = app.last_announcement
    assert last is not None
    assert (last.due, last.parts) == (
        at(19),
        ((Kind.END, "Ayşe Yılmaz"), (Kind.END, "Mehmet Demir")),
    )
    assert len(output.played) == 8


def test_end_that_a_delay_puts_in_the_minute_of_a_custom_announcement_is_read_with_it(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(CustomAnnouncement("Salon kapanıyor.", SEVEN_PM))
    wait_for_speech(app)

    app.set_end_delay(5)

    assert [(row.time, row.kind) for row in app.rows()] == [
        (SIX_PM, Kind.START),
        (SIX_PM, Kind.START),
        (SEVEN_PM, Kind.END),
        (SEVEN_PM, Kind.END),
        (SEVEN_PM, Kind.CUSTOM),
    ]
    app.start()
    clock.now = at(19)
    wait_for(app, lambda: statuses(app)[-1] is Status.PLAYED)

    last = app.last_announcement
    assert last is not None
    assert (last.due, last.parts) == (
        at(19),
        (
            (Kind.END, "Ayşe Yılmaz"),
            (Kind.END, "Mehmet Demir"),
            (Kind.CUSTOM, "Salon kapanıyor."),
        ),
    )
    assert len(output.played) == 4


def test_delay_takes_the_starts_out_of_the_minute_of_a_custom_announcement_and_into_one(
    app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    app.set_start_delay(2)
    app.set_end_delay(2)

    app.save_announcement(CustomAnnouncement("Hoş geldiniz.", SIX_PM))
    app.save_announcement(
        CustomAnnouncement("Isınmaya başlayın.", datetime.time(18, 2))
    )

    assert [(row.time, row.kind, whom(row)) for row in app.rows()] == [
        (SIX_PM, Kind.CUSTOM, "Hoş geldiniz."),
        (datetime.time(18, 2), Kind.START, "Ayşe Yılmaz"),
        (datetime.time(18, 2), Kind.START, "Mehmet Demir"),
        (datetime.time(18, 2), Kind.CUSTOM, "Isınmaya başlayın."),
        (datetime.time(18, 57), Kind.END, "Ayşe Yılmaz"),
        (datetime.time(18, 57), Kind.END, "Mehmet Demir"),
    ]


def test_change_of_the_delays_keeps_what_is_known_and_repeats_nothing(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(CustomAnnouncement("Günaydın.", datetime.time(9, 0)))
    wait_for_speech(app)
    app.start()
    clock.now = at(9)
    wait_for(app, lambda: statuses(app)[0] is Status.PLAYED)
    clock.now = at(18)
    wait_for(app, lambda: statuses(app) == [Status.PLAYED, *READ_AT_SIX])
    played = list(output.played)

    clock.now = at(18, 5)
    app.set_start_delay(10)
    app.set_end_delay(10)

    assert [(row.time, row.status) for row in app.rows()] == [
        (datetime.time(9, 0), Status.PLAYED),
        (datetime.time(18, 10), Status.PLAYED),
        (datetime.time(18, 10), Status.PLAYED),
        (datetime.time(19, 5), Status.NEXT),
        (datetime.time(19, 5), Status.NEXT),
    ]
    clock.now = at(18, 10)
    time.sleep(0.1)
    app.poll()
    assert output.played == played
    app.set_start_delay(0)
    app.set_end_delay(0)
    assert statuses(app) == [Status.PLAYED, *READ_AT_SIX]


def test_shorter_delay_announces_at_once_what_would_have_been_dropped(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.set_start_delay(5)
    wait_for_speech(app)
    app.start()
    clock.now = at(18, 2)
    time.sleep(0.1)
    app.poll()
    assert (statuses(app), output.played) == (UPCOMING, [])

    app.set_start_delay(0)

    wait_for(app, lambda: statuses(app) == READ_AT_SIX)
    assert len(output.played) == 3
    last = app.last_announcement
    assert last is not None
    assert last.due == at(18)


def test_shorter_delay_of_the_ends_announces_no_start_at_once(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    write_schedule(schedule, [*ROWS, ("Elif Şahin", "Pazartesi", "18:55")])
    app.choose_schedule(schedule)
    app.set_end_delay(5)
    wait_for_speech(app)
    clock.now = at(18, 57)
    app.start()
    time.sleep(0.1)
    app.poll()
    assert output.played == []

    app.set_end_delay(0)

    wait_for(app, lambda: app.last_announcement is not None)
    assert [(row.kind, whom(row), row.status) for row in app.rows()][2:5] == [
        (Kind.END, "Ayşe Yılmaz", Status.PLAYED),
        (Kind.END, "Mehmet Demir", Status.PLAYED),
        (Kind.START, "Elif Şahin", Status.PASSED),
    ]
    assert len(output.played) == 3
    last = app.last_announcement
    assert last is not None
    assert (last.due, last.parts) == (
        at(18, 55),
        ((Kind.END, "Ayşe Yılmaz"), (Kind.END, "Mehmet Demir")),
    )


def test_row_is_announced_again_on_request(
    app: App,
    schedule: Path,
    output: FakeOutput,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="announcer.app")
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app) == READ_AT_SIX)
    chime, _, mehmet = output.played
    clock.now = at(18, 7)

    assert app.replay(app.rows()[1])

    assert statuses(app) == [Status.PLAYED, Status.QUEUED, Status.NEXT, Status.NEXT]
    assert not app.replay(app.rows()[1])  # not while it is still to sound
    wait_for(app, lambda: statuses(app) == READ_AT_SIX)
    assert output.played[3:] == [chime, mehmet]
    last = app.last_announcement
    assert last is not None
    assert (last.due, last.parts, last.status) == (
        at(18),
        ((Kind.START, "Mehmet Demir"),),
        Status.PLAYED,
    )
    assert f'18:00 asked for again: "{started("Mehmet Demir")}"\n' in caplog.text


def test_one_of_two_custom_announcements_of_a_minute_is_announced_again_by_itself(
    app: App, output: FakeOutput, clock: FakeClock
) -> None:
    app.save_announcement(WATER)
    app.save_announcement(CustomAnnouncement("Salon kapanıyor.", FIVE_TO_SEVEN))
    wait_for_speech(app)
    app.start()
    clock.now = at(18, 55)
    wait_for(app, lambda: statuses(app) == [Status.PLAYED] * 2)
    chime, _, closing = output.played
    clock.now = at(18, 57)

    assert app.replay(app.rows()[1])

    assert statuses(app) == [Status.PLAYED, Status.QUEUED]
    wait_for(app, lambda: statuses(app) == [Status.PLAYED] * 2)
    assert output.played[3:] == [chime, closing]
    last = app.last_announcement
    assert last is not None
    assert (last.due, last.parts) == (
        at(18, 55),
        ((Kind.CUSTOM, "Salon kapanıyor."),),
    )


def test_row_whose_time_went_by_can_be_announced_afterwards(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock, tmp_path: Path
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    normal = stored_speech(tmp_path)
    app.set_speed(1.1)
    wait_for_speech(app)
    clock.now = at(18, 5)
    app.start()
    assert statuses(app) == [Status.PASSED, Status.PASSED, Status.NEXT, Status.NEXT]

    assert app.replay(app.rows()[0])
    wait_for(app, lambda: statuses(app)[0] is Status.PLAYED)

    assert statuses(app) == [Status.PLAYED, Status.PASSED, Status.NEXT, Status.NEXT]
    assert len(output.played) == 2
    assert not normal.intersection(output.played)  # at the speed in use


def test_row_is_not_read_again_while_what_was_at_its_time_is_sounding(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock, tmp_path: Path
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    clock.now = at(18, 5)
    app.start()
    output.endless = True
    assert app.replay(app.rows()[0])

    late = ("Can Öztürk", "Pazartesi", "18:00")
    app.choose_schedule(write_schedule(tmp_path / "more.xlsx", [*ROWS, late]))
    wait_for_speech(app)
    # The athlete's row still shows what sounds, the schedule changed or not.
    assert [(row.kind, whom(row), row.status) for row in app.rows()][:3] == [
        (Kind.START, "Ayşe Yılmaz", Status.QUEUED),
        (Kind.START, "Mehmet Demir", Status.PASSED),
        (Kind.START, "Can Öztürk", Status.PASSED),
    ]

    assert not app.replay(app.rows()[0])

    output.endless = False
    wait_for(app, lambda: app.last_announcement is not None)
    last = app.last_announcement
    assert last is not None
    assert last.parts == ((Kind.START, "Ayşe Yılmaz"),)
    assert app.replay(app.rows()[0])


def test_start_speaks_at_a_speed_that_has_speech(
    make_app: Callable[[], App],
    schedule: Path,
    provider: FakeProvider,
    output: FakeOutput,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = make_app()
    first.choose_schedule(schedule)
    wait_for_speech(first)
    provider.offline = True
    first.set_speed(0.8)
    wait_for_speech(first)
    first.stop()

    app = make_app()
    with caplog.at_level(logging.WARNING, logger="announcer.app"):
        app.start()
    wait_for_speech(app)

    assert (app.settings.speed, app.spoken_speed) == (0.8, 1.0)
    assert "No speech yet at speed 0.8; announcing at 1.0 until there is" in (
        caplog.text
    )
    clock.now = at(18)
    wait_for(app, lambda: statuses(app) == READ_AT_SIX)
    assert len(output.played) == 3

    provider.offline = False
    app.refresh()
    wait_for_speech(app)
    assert app.spoken_speed == 0.8


def test_row_that_cannot_be_replayed_is_left_alone(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    waiting = app.rows()[0]
    clock.now = at(18, 5)
    passed = app.rows()[0]
    assert (waiting.status, passed.status) == (Status.NEXT, Status.PASSED)

    assert not app.replay(waiting)  # no longer what the row is
    zeynep = (at(18), Kind.START, "Zeynep Kaya")  # trains on Tuesday
    assert not app.replay(
        Row(SIX_PM, Kind.START, started("Zeynep Kaya"), Status.PASSED, zeynep)
    )
    app.set_start_delay(10)
    assert not app.replay(passed)  # it has moved to a time that is still to come

    assert statuses(app) == UPCOMING


def test_outcome_does_not_pass_to_what_is_put_at_that_time_later(
    app: App, clock: FakeClock
) -> None:
    app.save_announcement(WATER)
    wait_for_speech(app)
    app.start()
    clock.now = at(18, 55)
    wait_for(app, lambda: statuses(app) == [Status.PLAYED])

    clock.now = at(19)
    app.save_announcement(
        CustomAnnouncement("Salon kapanıyor.", WATER.time), replacing=0
    )

    assert statuses(app) == [Status.PASSED]


def test_actions_and_outcomes_are_logged_with_their_cause(
    app: App,
    schedule: Path,
    provider: FakeProvider,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="announcer")
    provider.unspeakable = {DEFAULT_TEMPLATE.replace("{ad_soyad}", "Mehmet Demir")}
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    wait_for_speech(app)
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app)[:2] == [Status.PLAYED, Status.NO_SPEECH])
    last = app.last_announcement
    assert last is not None
    assert last.status is Status.PARTLY_PLAYED
    clock.now = at(20)
    wait_for(app, lambda: statuses(app)[2] is Status.ASLEEP)

    log = caplog.text
    assert f"Version {VERSION} started" in log
    assert "Schedule loaded from" in log
    assert "3 session(s), 0 row(s) skipped" in log
    assert 'Custom announcement added: "Su içmeyi unutmayın." at 18:55 on every' in log
    assert 'No speech for 1 sentence(s), the first of them "Mehmet Demir, ' in log
    assert '18:00 announced: "Ayşe Yılmaz, antrenmanınız başlamıştır.' in log
    assert '18:00 skipped, no stored speech: "Mehmet Demir, antrenmanınız' in log
    assert "2026-11-09 18:55 skipped, the computer was asleep" in log
    app.set_speed(0.9)
    app.set_start_delay(3)
    app.set_end_delay(5)
    assert "Speed changed to 0.9" in caplog.text
    assert "Delay of the starts changed to 3 minute(s)" in caplog.text
    assert "Delay of the ends changed to 5 minute(s)" in caplog.text


def test_clear_puts_the_app_back_to_how_it_is_at_a_first_start(
    make_app: Callable[[], App], schedule: Path, settings_file: Path, clock: FakeClock
) -> None:
    settings_file.parent.mkdir(parents=True)
    stored = {
        "custom_announcements": [{"text": "Duyuru", "time": "25:00", "weekdays": [0]}]
    }
    settings_file.write_text(json.dumps(stored), encoding="utf-8")
    settings_file.with_name("changes.json").write_text("{ not json", encoding="utf-8")
    write_schedule(schedule, [*ROWS, ("Can Öztürk", "Pazartesi", "25:00")])
    app = make_app()
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    app.set_templates(WELCOME, GOODBYE)
    app.set_speed(0.9)
    wait_for_speech(app)
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: app.last_announcement is not None)
    assert (
        app.dropped_announcements,
        app.settings_backed_up,
        app.changes_unreadable,
        app.changes_backed_up,
        len(app.skipped_rows),
    ) == (1, True, True, True, 1)

    app.clear()

    assert app.settings == Settings()
    assert (app.spoken_template, app.spoken_end_template, app.spoken_speed) == (
        DEFAULT_TEMPLATE,
        DEFAULT_END_TEMPLATE,
        1.0,
    )
    assert (app.rows(), app.timetable.slots) == ((), ())
    assert (
        app.dropped_announcements,
        app.settings_backed_up,
        app.changes_unreadable,
        app.changes_backed_up,
        app.skipped_rows,
    ) == (0, False, False, False, ())
    assert (app.schedule_error, app.refreshed_at) == (None, None)
    assert app.last_announcement is None
    assert (app.generating, app.unspoken) == (False, ())


def test_clear_deletes_the_settings_the_changes_their_copies_and_the_stored_speech(
    make_app: Callable[[], App], schedule: Path, settings_file: Path, tmp_path: Path
) -> None:
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text("{ not json", encoding="utf-8")
    changes_file = settings_file.with_name("changes.json")
    changes_file.write_text("{ not json", encoding="utf-8")
    app = make_app()
    app.choose_schedule(schedule)
    wait_for_speech(app)
    files = [
        settings_file,
        backup_copy(settings_file),
        changes_file,
        backup_copy(changes_file),
    ]
    assert all(file.exists() for file in files)
    assert len(stored_speech(tmp_path)) == 6

    app.clear()

    assert not any(file.exists() for file in files)
    assert stored_speech(tmp_path) == set()
    assert schedule.exists()
    app.stop()
    again = make_app()
    again.start()
    assert (again.settings, again.rows(), again.refreshed_at) == (Settings(), (), None)
    assert not (again.settings_unreadable or again.settings_backed_up)
    assert not (again.changes_unreadable or again.changes_backed_up)
    assert not again.generating


def test_clear_cuts_off_what_is_sounding_and_what_is_queued_behind_it_is_never_heard(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    output.endless = True
    clock.now = at(18)
    wait_for(app, lambda: app.sounding)
    clock.now = at(18, 55)
    wait_for(app, lambda: statuses(app)[2:] == [Status.QUEUED] * 2)

    app.clear()

    assert not app.sounding
    output.endless = False
    app.preview("Deneme anonsu.")
    wait_for(app, lambda: app.test_status is Status.PLAYED)
    assert output.played[:2] == [CHIME_PATH] * 2
    assert len(output.played) == 3
    assert app.last_announcement is None
    assert app.rows() == ()


def test_slot_that_came_due_just_before_a_clear_is_not_taken_in_after_it(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    output.endless = True
    clock.now = at(18)
    latest = time.monotonic() + 5
    while not output.played and time.monotonic() < latest:
        time.sleep(0.005)
    assert output.played

    app.clear()

    output.endless = False
    app.choose_schedule(schedule)
    app.preview("Deneme anonsu.")
    wait_for(app, lambda: app.test_status is Status.PLAYED)
    assert statuses(app)[:2] == [Status.NEXT] * 2
    assert (app.sounding, app.last_announcement) == (False, None)


def test_clear_straight_after_a_schedule_is_chosen_stores_none_of_its_speech(
    app: App, schedule: Path, tmp_path: Path
) -> None:
    app.choose_schedule(schedule)

    app.clear()

    wait_for_speech(app)
    assert not (tmp_path / "audio").exists()
    assert (app.unspoken, app.rows()) == ((), ())


def test_clear_while_speech_is_generated_stores_none_of_it_and_leaves_nothing_unspoken(
    app: App,
    provider: FakeProvider,
    clock: FakeClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.save_announcement(WATER)
    wait_for_speech(app)
    synthesize = provider.synthesize
    asked, cleared = threading.Event(), threading.Event()

    def answer_late(text: str, voice: str, speed: float) -> bytes:
        asked.set()
        cleared.wait(5)
        return synthesize(text, voice, speed)

    monkeypatch.setattr(provider, "synthesize", answer_late)
    app.save_announcement(CustomAnnouncement("Salon kapanıyor.", SEVEN_PM))
    assert app.generating
    assert asked.wait(5)

    app.clear()
    cleared.set()
    wait_for_speech(app)

    assert app.unspoken == ()
    assert stored_speech(tmp_path) == set()
    answered = len(provider.calls)
    clock.now += datetime.timedelta(minutes=5)
    assert not app.poll()
    assert (app.generating, len(provider.calls)) == (False, answered)


def test_clear_waits_for_the_audio_output_to_let_go_of_the_stored_speech(
    app: App, schedule: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    unlink = Path.unlink
    held = [True]

    def let_go_late(path: Path, missing_ok: bool = False) -> None:
        if held and path.parent == tmp_path / "audio":
            held.clear()
            raise PermissionError("still open in the audio output")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", let_go_late)

    app.clear()

    assert not held
    assert stored_speech(tmp_path) == set()
    assert app.settings == Settings()


def test_clear_that_cannot_delete_a_file_is_raised_and_keeps_the_settings(
    app: App,
    schedule: Path,
    settings_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    wait_for_speech(app)
    settings, speech = app.settings, stored_speech(tmp_path)
    unlink = Path.unlink

    def held(path: Path, missing_ok: bool = False) -> None:
        if path == settings_file:
            raise PermissionError("held by another program")
        unlink(path, missing_ok=missing_ok)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", held)
        with pytest.raises(OSError):
            app.clear()

    assert app.settings == settings
    assert load_settings(settings_file).settings == settings
    assert len(app.rows()) == 5
    assert app.generating
    wait_for_speech(app)
    assert app.unspoken == ()
    assert stored_speech(tmp_path) == speech


def test_app_announces_as_usual_once_a_schedule_is_chosen_after_a_clear(
    app: App,
    schedule: Path,
    provider: FakeProvider,
    output: FakeOutput,
    clock: FakeClock,
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.start()
    app.clear()
    asked = len(provider.calls)

    app.choose_schedule(schedule)

    assert app.generating
    wait_for_speech(app)
    assert (app.unspoken, len(provider.calls)) == ((), asked + 6)
    assert statuses(app) == UPCOMING
    clock.now = at(18, 0, 1)
    wait_for(app, lambda: statuses(app) == READ_AT_SIX)
    assert len(output.played) == 3
    last = app.last_announcement
    assert last is not None
    assert (last.due, last.status) == (at(18), Status.PLAYED)
