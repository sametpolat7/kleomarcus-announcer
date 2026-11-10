import datetime
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from announcer.app import TEST_NAME, App, SlotTakenError, backup_copy
from announcer.chime import CHIME_PATH
from announcer.loader import ScheduleFileError, UnreadableFileError
from announcer.models import CustomAnnouncement
from announcer.settings import Settings, load_settings, save_settings
from announcer.template import DEFAULT_TEMPLATE
from announcer.timetable import SlotHolder
from announcer.today import Status
from support import (
    ROWS,
    FakeClock,
    FakeOutput,
    FakeProvider,
    at,
    wait_for,
    write_schedule,
)

SIX_PM, SEVEN_PM = datetime.time(18, 0), datetime.time(19, 0)
WATER = CustomAnnouncement("Su içmeyi unutmayın.", SEVEN_PM)


def wait_for_speech(app: App) -> None:
    wait_for(app, lambda: not app.generating)


def statuses(app: App) -> list[Status]:
    return [row.status for row in app.rows()]


def test_choosing_a_schedule_loads_it_remembers_the_file_and_generates_its_speech(
    app: App,
    schedule: Path,
    settings_file: Path,
    clock: FakeClock,
    provider: FakeProvider,
) -> None:
    app.choose_schedule(schedule)

    assert [row.summary for row in app.rows()] == ["Ayşe Yılmaz, Mehmet Demir"]
    assert app.refreshed_at == clock.now
    assert load_settings(settings_file).settings.schedule_path == schedule
    assert app.generating
    wait_for_speech(app)

    assert app.unspoken == ()
    assert sorted(text for text, _ in provider.calls) == sorted(
        DEFAULT_TEMPLATE.replace("{ad_soyad}", name) for name, _, _ in ROWS
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


def test_schedule_file_that_was_saved_is_read_again_by_itself(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    app.start()
    clock.now = at(9)

    write_schedule(schedule, [*ROWS, ("Elif Şahin", "Pazartesi", "20:30")])
    wait_for(app, lambda: len(app.rows()) == 2)

    assert [row.summary for row in app.rows()] == [
        "Ayşe Yılmaz, Mehmet Demir",
        "Elif Şahin",
    ]
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
    assert len(app.rows()) == 1


def test_schedule_file_that_disappears_is_reported_and_read_when_it_returns(
    app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    app.start()

    schedule.unlink()
    wait_for(app, lambda: app.schedule_error is not None)
    assert len(app.rows()) == 1

    write_schedule(schedule, ROWS[:1])
    wait_for(app, lambda: app.schedule_error is None)
    assert [row.summary for row in app.rows()] == ["Ayşe Yılmaz"]


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
    assert len(provider.calls) == asked + 3


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
    app: App,
    schedule: Path,
    output: FakeOutput,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    clock.now = at(18, 0, 5)

    with caplog.at_level(logging.WARNING, logger="announcer.app"):
        app.start()

    assert statuses(app) == [Status.PASSED]
    assert "18:00 not announced, its minute had begun when the app started" in (
        caplog.text
    )
    clock.now = at(18, 0, 6)
    time.sleep(0.1)
    app.poll()
    assert output.played == []


def test_new_template_is_stored_and_spoken(
    app: App, schedule: Path, settings_file: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)

    app.set_template("Hoş geldiniz, {ad_soyad}.")
    wait_for_speech(app)

    assert load_settings(settings_file).settings.template == "Hoş geldiniz, {ad_soyad}."
    assert app.timetable.slots[0].texts[0] == "Hoş geldiniz, Ayşe Yılmaz."
    assert ("Hoş geldiniz, Zeynep Kaya.", app.settings.voice) in provider.calls
    assert app.unspoken == ()


def test_template_changed_while_speech_is_generated_is_spoken_too(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    assert app.generating
    app.set_template("Hoş geldiniz, {ad_soyad}.")
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


def test_custom_announcement_cannot_share_a_slot(app: App, schedule: Path) -> None:
    app.choose_schedule(schedule)
    app.save_announcement(WATER)

    with pytest.raises(SlotTakenError) as with_athletes:
        app.save_announcement(
            CustomAnnouncement("Salon kapanıyor.", SIX_PM, frozenset({0, 3}))
        )
    with pytest.raises(SlotTakenError) as with_another:
        app.save_announcement(
            CustomAnnouncement("Salon kapanıyor.", SEVEN_PM, frozenset({6}))
        )

    assert [
        (conflict.weekday, conflict.holder)
        for error in (with_athletes, with_another)
        for conflict in error.value.conflicts
    ] == [(0, SlotHolder.ATHLETES), (6, SlotHolder.CUSTOM_ANNOUNCEMENT)]
    assert app.settings.custom_announcements == (WATER,)


def test_custom_announcement_is_changed_in_place(app: App) -> None:
    closing = CustomAnnouncement("Salon kapanıyor.", datetime.time(21, 45))
    app.save_announcement(WATER)
    app.save_announcement(closing)

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
        app.set_template("Hoş geldiniz, {ad_soyad}.")
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

    app.start()
    clock.now = at(18, 0, 1)
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

    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app) == [Status.NO_SPEECH])

    assert output.played == []


def test_slot_that_the_output_cannot_play_is_shown_as_failed(
    app: App, schedule: Path, output: FakeOutput, clock: FakeClock
) -> None:
    output.no_device = True
    app.choose_schedule(schedule)
    wait_for_speech(app)

    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app) == [Status.NO_OUTPUT])

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
    app.start()
    clock.now = at(19)
    wait_for(app, lambda: statuses(app)[-1] is Status.PLAYED)

    clock.now = at(19) + datetime.timedelta(days=7)
    wait_for(app, lambda: statuses(app)[-1] is Status.PLAYED)

    assert statuses(app) == [Status.ASLEEP, Status.PLAYED]


def test_test_announcement_speaks_the_template_with_a_made_up_name(
    app: App, provider: FakeProvider, output: FakeOutput
) -> None:
    app.start()
    with pytest.raises(ValueError, match="placeholder"):
        app.test("Merhaba.")
    assert app.test_status is None

    app.test("Merhaba {ad_soyad}.")
    assert app.test_status is Status.PLAYING
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
    app.set_template("Hoş geldiniz, {ad_soyad}.")
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
    old = app.timetable.slots[0].texts

    provider.offline = True
    app.set_template("Hoş geldiniz, {ad_soyad}.")
    wait_for_speech(app)

    assert app.settings.template == "Hoş geldiniz, {ad_soyad}."
    assert app.spoken_template == DEFAULT_TEMPLATE
    assert app.timetable.slots[0].texts == old
    assert len(app.unspoken) == 3
    app.start()
    clock.now = at(18)
    wait_for(app, lambda: statuses(app) == [Status.PLAYED])
    assert len(output.played) == 3  # the chime and both athletes

    provider.offline = False
    app.refresh()
    wait_for_speech(app)

    assert app.spoken_template == "Hoş geldiniz, {ad_soyad}."
    assert app.timetable.slots[0].texts[0] == "Hoş geldiniz, Ayşe Yılmaz."
    assert not app.unspoken


def test_template_with_stored_speech_is_spoken_at_once(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    wait_for_speech(app)
    app.set_template("Hoş geldiniz, {ad_soyad}.")
    wait_for_speech(app)
    provider.offline = True

    app.set_template(DEFAULT_TEMPLATE)

    assert not app.generating
    assert app.spoken_template == DEFAULT_TEMPLATE


def test_outcome_does_not_pass_to_what_is_put_at_that_time_later(
    app: App, clock: FakeClock
) -> None:
    app.save_announcement(WATER)
    wait_for_speech(app)
    app.start()
    clock.now = at(19)
    wait_for(app, lambda: statuses(app) == [Status.PLAYED])

    clock.now = at(19, 5)
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
    wait_for(app, lambda: statuses(app)[0] is Status.PARTLY_PLAYED)
    clock.now = at(20)
    wait_for(app, lambda: statuses(app)[1] is Status.ASLEEP)

    log = caplog.text
    assert "Schedule loaded from" in log
    assert "3 session(s), 0 row(s) skipped" in log
    assert 'Custom announcement added: "Su içmeyi unutmayın." at 19:00 on every' in log
    assert 'No speech for 1 sentence(s), the first of them "Mehmet Demir, ' in log
    assert '18:00 announced: "Ayşe Yılmaz, antrenmanınız başlamıştır.' in log
    assert '18:00 skipped, no stored speech: "Mehmet Demir, antrenmanınız' in log
    assert "2026-11-09 19:00 skipped, the computer was asleep" in log
