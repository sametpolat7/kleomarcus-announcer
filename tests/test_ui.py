"""Tests for the window and the dialog, driven without a person at the screen.

They need a display, as tkinter does; without one they are skipped.
"""

import datetime
import gc
import logging
import time
import tkinter as tk
from collections.abc import Callable, Iterator
from pathlib import Path
from tkinter import filedialog, messagebox

import pytest

from announcer.app import App
from announcer.models import ALL_WEEKDAYS, CustomAnnouncement
from announcer.template import DEFAULT_TEMPLATE
from announcer.today import Status
from announcer.ui import main_window
from announcer.ui.announcement_dialog import AnnouncementDialog
from announcer.ui.main_window import MainWindow
from support import ROWS, FakeClock, FakeProvider, at, wait_for, write_schedule

WATER = CustomAnnouncement("Su içmeyi unutmayın.", datetime.time(19, 0))
CLOSING = CustomAnnouncement("Salon kapanıyor.", datetime.time(9, 30), frozenset({5}))


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The messages of the error boxes that would have been shown."""
    shown: list[str] = []

    def showerror(title: str, message: str, **options: object) -> str:
        shown.append(message)
        return "ok"

    monkeypatch.setattr(messagebox, "showerror", showerror)
    return shown


@pytest.fixture
def window(
    app: App,
    tmp_path: Path,
    clock: FakeClock,
    alerts: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[MainWindow]:
    """The main window over the app of the fakes, closed after the test."""
    monkeypatch.setattr(main_window, "_POLL", 5)
    try:
        window = MainWindow(app, tmp_path / "announcer.log", clock)
    except tk.TclError as error:
        pytest.skip(f"tkinter cannot open a window here: {error}")
    window.root.withdraw()  # everything works the same without being on screen
    yield window
    if not window.closed:
        window.close()
    # tkinter has to be freed on this thread, not on one of the app's threads
    # that happens to collect garbage later.
    gc.collect()


def pump(window: MainWindow, condition: Callable[[], bool]) -> None:
    """Let the window run until ``condition`` holds; fail if it never does."""
    deadline = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        window.root.update()
        time.sleep(0.005)


def today(window: MainWindow) -> list[tuple[str, ...]]:
    table = window.today
    return [tuple(table.item(row, "values")) for row in table.get_children()]


def announcements(window: MainWindow) -> list[tuple[str, ...]]:
    table = window.announcements
    return [tuple(table.item(row, "values")) for row in table.get_children()]


def warnings(window: MainWindow) -> str:
    return window.warnings.get("1.0", "end").strip()


def choose(
    window: MainWindow, path: Path | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Press "Dosya Seç" and pick ``path`` in the file dialog, or cancel it."""
    monkeypatch.setattr(
        filedialog, "askopenfilename", lambda **options: str(path) if path else ""
    )
    window.choose_button.invoke()


def test_fresh_window_asks_for_a_schedule(window: MainWindow) -> None:
    assert window.schedule_path.get() == ""
    assert window.refreshed.get() == "Son yenileme: —"
    assert window.today_title.get() == "9 Kasım 2026 Pazartesi"
    assert today(window) == [("", "", "Bugün için anons yok.", "")]
    assert announcements(window) == [("", "", "Özel anons yok.")]
    assert window.time.get() == "08:00:00"
    assert window.last_announcement.get() == "Son anons: —"
    assert window.template.get() == DEFAULT_TEMPLATE
    assert warnings(window).startswith("• Program dosyası seçilmedi.")


def test_choosing_a_schedule_shows_todays_announcements(
    window: MainWindow, schedule: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    choose(window, schedule, monkeypatch)

    assert window.schedule_path.get() == str(schedule)
    assert window.refreshed.get() == "Son yenileme: 09.11.2026 08:00:00"
    assert today(window) == [
        ("18:00", "Sporcu", "Ayşe Yılmaz, Mehmet Demir", "Sıradaki")
    ]
    pump(window, lambda: warnings(window) == "")
    assert not window.warning_box.winfo_manager()  # the box is taken away


def test_cancelling_the_file_dialog_changes_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, alerts: list[str]
) -> None:
    choose(window, None, monkeypatch)

    assert window.schedule_path.get() == ""
    assert alerts == []


def test_schedule_that_cannot_be_used_gives_an_alert(
    window: MainWindow,
    schedule: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[str],
) -> None:
    choose(window, schedule, monkeypatch)
    broken = tmp_path / "broken.xlsx"
    broken.write_text("not a workbook")

    choose(window, broken, monkeypatch)

    assert alerts == [
        "Program dosyası okunamadı. Geçerli bir Excel (.xlsx) dosyası olmalıdır."
    ]
    assert window.schedule_path.get() == str(schedule)
    assert len(today(window)) == 1


def test_refresh_button_reads_the_file_again(
    window: MainWindow,
    schedule: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    write_schedule(schedule, [*ROWS, ("Elif Şahin", "Pazartesi", "20:30")])
    clock.now = at(9, 15)

    window.refresh_button.invoke()

    assert today(window)[1] == ("20:30", "Sporcu", "Elif Şahin", "Bekliyor")
    assert window.refreshed.get() == "Son yenileme: 09.11.2026 09:15:00"


def test_status_follows_the_clock(
    window: MainWindow,
    schedule: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)

    clock.now = at(18, 5)
    pump(window, lambda: today(window)[0][3] == "Saati geçti")
    clock.now = at(0, 0) + datetime.timedelta(days=1)
    pump(window, lambda: window.today_title.get() == "10 Kasım 2026 Salı")

    assert today(window) == [("20:00", "Sporcu", "Zeynep Kaya", "Sıradaki")]


def test_due_slot_is_shown_as_played(
    window: MainWindow,
    app: App,
    schedule: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    wait_for(app, lambda: not app.generating)

    clock.now = at(18)
    app.start()
    pump(window, lambda: today(window)[0][3] == "Okundu")

    assert window.last_announcement.get() == (
        "Son anons: 18:00 · Ayşe Yılmaz, Mehmet Demir · Okundu"
    )


def test_speech_that_cannot_be_generated_is_shown_as_a_warning(
    window: MainWindow,
    schedule: Path,
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider.offline = True

    choose(window, schedule, monkeypatch)
    assert window.activity.get() == "Sesler oluşturuluyor…"
    pump(window, lambda: window.activity.get() == "")

    assert warnings(window).startswith("• 3 anonsun sesi oluşturulamadı.")


def test_clock_in_the_window_follows_the_seconds(
    window: MainWindow, clock: FakeClock
) -> None:
    clock.now = at(8, 0, 7)

    pump(window, lambda: window.time.get() == "08:00:07")


def test_warnings_show_in_a_box_that_grows_to_four_lines(
    window: MainWindow, app: App, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert window.warning_box.winfo_manager() == "grid"
    assert window.warnings.cget("height") == 1

    typos = [("", "Funday", "akşam")] * 6
    choose(window, write_schedule(tmp_path / "typos.xlsx", typos), monkeypatch)

    assert warnings(window).count("satır atlandı") == 6
    assert window.warnings.cget("height") == 4


def test_template_can_be_saved_only_while_it_is_changed(
    window: MainWindow, app: App
) -> None:
    assert window.save_template_button.instate(["disabled"])

    window.template.set("Hoş geldiniz, {ad}.")
    assert window.save_template_button.instate(["!disabled"])

    window.save_template_button.invoke()
    assert app.settings.template == "Hoş geldiniz, {ad}."
    assert window.save_template_button.instate(["disabled"])

    window.template.set(f"  {app.settings.template}  ")
    assert window.save_template_button.instate(["disabled"])


def test_closing_asks_first(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter([False, True])
    questions: list[str] = []

    def askyesno(title: str, message: str, **options: object) -> bool:
        questions.append(message)
        return next(answers)

    monkeypatch.setattr(messagebox, "askyesno", askyesno)

    window.ask_to_close()
    assert not window.closed

    window.ask_to_close()
    assert window.closed
    assert questions == ["Uygulama kapatılırsa anonslar okunmaz. Kapatılsın mı?"] * 2


def test_placeholder_row_cannot_be_changed_or_deleted(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        AnnouncementDialog, "wait", lambda dialog: pytest.fail("opened")
    )
    window.announcements.selection_set("none")
    window.root.update()

    assert window.edit_button.instate(["disabled"])
    assert window.delete_button.instate(["disabled"])
    window.edit()
    window.delete()


def test_template_is_saved_with_the_button(window: MainWindow, app: App) -> None:
    window.template.set("  Hoş geldiniz, {ad}.  ")

    window.save_template_button.invoke()

    assert app.settings.template == "Hoş geldiniz, {ad}."


def test_template_without_the_placeholder_gives_an_alert(
    window: MainWindow, app: App, alerts: list[str]
) -> None:
    window.template.set("Hoş geldiniz.")

    window.save_template_button.invoke()
    window.test_button.invoke()

    assert alerts == ["Şablonda “{ad}” yer tutucusu bulunmalıdır."] * 2
    assert app.settings.template == DEFAULT_TEMPLATE
    assert app.test_status is None
    # What was typed stays in the box, to be corrected.
    assert window.template.get() == "Hoş geldiniz."


def test_test_button_announces_what_is_typed_without_saving_it(
    window: MainWindow, app: App, provider: FakeProvider
) -> None:
    app.start()
    window.template.set("Merhaba {ad}.")

    window.test_button.invoke()
    assert window.activity.get() == "Test anonsu hazırlanıyor…"
    pump(window, lambda: window.activity.get() == "Test anonsu okundu.")

    assert [text for text, _ in provider.calls] == ["Merhaba Ayşe Yılmaz."]
    assert app.settings.template == DEFAULT_TEMPLATE
    assert window.template.get() == "Merhaba {ad}."


def test_custom_announcements_are_listed_by_time(window: MainWindow, app: App) -> None:
    app.save_announcement(WATER)
    app.save_announcement(CLOSING)

    window.show()

    assert announcements(window) == [
        ("09:30", "Cumartesi", "Salon kapanıyor."),
        ("19:00", "Her gün", "Su içmeyi unutmayın."),
    ]


def test_edit_and_delete_need_a_selection(window: MainWindow, app: App) -> None:
    app.save_announcement(WATER)
    window.show()
    assert window.edit_button.instate(["disabled"])
    assert window.delete_button.instate(["disabled"])

    window.announcements.selection_set("0")
    window.root.update()

    assert window.edit_button.instate(["!disabled"])
    assert window.delete_button.instate(["!disabled"])


def test_delete_asks_first(
    window: MainWindow, app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.save_announcement(WATER)
    app.save_announcement(CLOSING)
    window.show()
    answers = iter([False, True])
    questions: list[str] = []

    def askyesno(title: str, message: str, **options: object) -> bool:
        questions.append(message)
        return next(answers)

    monkeypatch.setattr(messagebox, "askyesno", askyesno)

    window.announcements.selection_set("1")
    window.root.update()
    window.delete_button.invoke()
    assert CLOSING in app.settings.custom_announcements

    window.delete_button.invoke()
    assert app.settings.custom_announcements == (WATER,)
    assert questions == ["“Salon kapanıyor.” anonsu silinsin mi?"] * 2
    assert announcements(window) == [("19:00", "Her gün", "Su içmeyi unutmayın.")]
    assert window.delete_button.instate(["disabled"])


def test_second_copy_only_says_that_the_app_is_running(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(
        messagebox, "showinfo", lambda title, message, **options: shown.append(message)
    )

    main_window.tell_already_running()

    assert shown == ["Uygulama zaten çalışıyor."]


def test_log_button_opens_the_log_file(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []
    monkeypatch.setattr(main_window, "open_file", opened.append)
    (tmp_path / "announcer.log").write_text("a line")

    window.log_button.invoke()

    assert opened == [tmp_path / "announcer.log"]


def test_log_button_says_so_when_there_is_no_log_file(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, alerts: list[str]
) -> None:
    opened: list[Path] = []
    monkeypatch.setattr(main_window, "open_file", opened.append)

    window.log_button.invoke()

    assert (opened, alerts) == ([], ["Kayıt dosyası bulunamadı."])


def test_window_can_be_closed_while_the_dialog_is_open(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, alerts: list[str]
) -> None:
    # Closing the main window ends the dialog's wait.
    monkeypatch.setattr(AnnouncementDialog, "wait", lambda dialog: window.close())

    window.add()

    assert window.closed
    assert alerts == []


def test_custom_announcements_keep_their_place_while_nothing_changes(
    window: MainWindow, app: App
) -> None:
    app.save_announcement(WATER)
    app.save_announcement(CLOSING)
    window.show()
    window.announcements.focus("1")

    window.show()

    assert window.announcements.focus() == "1"


def test_error_in_an_action_is_logged_and_told(
    window: MainWindow,
    app: App,
    alerts: list[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail() -> None:
        raise RuntimeError("the refresh broke")

    monkeypatch.setattr(app, "refresh", fail)

    with caplog.at_level(logging.ERROR):
        window.refresh_button.invoke()

    assert alerts == ["Beklenmeyen bir hata oluştu. Ayrıntılar kayıt dosyasındadır."]
    assert "An action in the window failed" in caplog.text
    assert "RuntimeError: the refresh broke" in caplog.text


def test_window_keeps_following_the_app_after_a_failed_update(
    window: MainWindow,
    app: App,
    schedule: Path,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    polls = iter([RuntimeError("the first look broke")] * 3)

    def poll() -> bool:
        for error in polls:
            raise error
        return App.poll(app)

    monkeypatch.setattr(app, "poll", poll)

    with caplog.at_level(logging.ERROR):
        choose(window, schedule, monkeypatch)
        assert window.activity.get() == "Sesler oluşturuluyor…"
        pump(window, lambda: "Bringing the window up to date failed" in caplog.text)
        # The next looks still take in that the speech is ready.
        pump(window, lambda: window.activity.get() == "")

    # An error that stays is logged once, not four times a second.
    assert caplog.text.count("Bringing the window up to date failed") == 1
    assert alerts == []


@pytest.fixture
def make_dialog(
    window: MainWindow, app: App
) -> Iterator[Callable[[int | None], AnnouncementDialog]]:
    """Open dialogs over the window; they are closed after the test."""
    dialogs: list[AnnouncementDialog] = []

    def make(index: int | None = None) -> AnnouncementDialog:
        dialog = AnnouncementDialog(window.root, app, index)
        dialog.window.withdraw()
        dialogs.append(dialog)
        return dialog

    yield make
    for dialog in dialogs:
        if dialog.window.winfo_exists():
            dialog.window.destroy()


def test_new_announcement_starts_empty_at_noon_every_day(
    make_dialog: Callable[[], AnnouncementDialog],
) -> None:
    dialog = make_dialog()

    assert dialog.text.get() == ""
    assert (dialog.hour.get(), dialog.minute.get()) == ("12", "00")
    assert dialog.every_day.get()


def test_dialog_adds_an_announcement_and_closes(
    make_dialog: Callable[[], AnnouncementDialog], app: App
) -> None:
    dialog = make_dialog()
    dialog.text.set("  Lütfen salonu temiz tutunuz.  ")
    dialog.hour.set("15")
    dialog.minute.set("30")

    dialog.save()

    assert app.settings.custom_announcements == (
        CustomAnnouncement("Lütfen salonu temiz tutunuz.", datetime.time(15, 30)),
    )
    assert not dialog.window.winfo_exists()


def test_dialog_saves_the_ticked_days(
    make_dialog: Callable[[], AnnouncementDialog], app: App
) -> None:
    dialog = make_dialog()
    dialog.text.set("Lütfen ekipmanları yerlerine bırakınız.")
    dialog.hour.set("19")
    dialog.minute.set("55")
    dialog.every_day.set(False)
    for day in ALL_WEEKDAYS:
        dialog.days[day].set(day in (1, 3))

    dialog.save()

    [saved] = app.settings.custom_announcements
    assert (saved.time, saved.weekdays) == (datetime.time(19, 55), frozenset({1, 3}))


@pytest.mark.parametrize(
    ("text", "hour", "minute", "days", "message"),
    [
        ("   ", "12", "00", {0}, "Anons metni boş olamaz."),
        ("Duyuru", "24", "00", {0}, "Saat 00:00 ile 23:59 arasında olmalıdır."),
        ("Duyuru", "12", "60", {0}, "Saat 00:00 ile 23:59 arasında olmalıdır."),
        ("Duyuru", "on", "00", {0}, "Saat 00:00 ile 23:59 arasında olmalıdır."),
        ("Duyuru", "", "", {0}, "Saat 00:00 ile 23:59 arasında olmalıdır."),
        ("Duyuru", "12", "00", set(), "En az bir gün seçin."),
    ],
)
def test_dialog_stays_open_until_what_is_entered_is_valid(
    make_dialog: Callable[[], AnnouncementDialog],
    app: App,
    alerts: list[str],
    text: str,
    hour: str,
    minute: str,
    days: set[int],
    message: str,
) -> None:
    dialog = make_dialog()
    dialog.text.set(text)
    dialog.hour.set(hour)
    dialog.minute.set(minute)
    dialog.every_day.set(False)
    for day in ALL_WEEKDAYS:
        dialog.days[day].set(day in days)

    dialog.save()

    assert alerts == [message]
    assert app.settings.custom_announcements == ()
    assert dialog.window.winfo_exists()


def test_dialog_blocks_an_announcement_whose_slot_is_taken(
    make_dialog: Callable[[], AnnouncementDialog],
    app: App,
    schedule: Path,
    alerts: list[str],
) -> None:
    app.choose_schedule(schedule)
    dialog = make_dialog()
    dialog.text.set("Salon kapanıyor.")
    dialog.hour.set("18")
    dialog.minute.set("00")

    dialog.save()

    assert alerts == [
        "Bu saat dolu olduğu için anons kaydedilemedi:\n"
        "• Pazartesi 18:00: sporcu anonsu var"
    ]
    assert app.settings.custom_announcements == ()
    assert dialog.window.winfo_exists()


def test_dialog_shows_the_announcement_it_changes(
    make_dialog: Callable[[int | None], AnnouncementDialog], app: App
) -> None:
    app.save_announcement(WATER)
    app.save_announcement(CLOSING)

    dialog = make_dialog(1)

    assert dialog.text.get() == "Salon kapanıyor."
    assert (dialog.hour.get(), dialog.minute.get()) == ("09", "30")
    assert not dialog.every_day.get()
    assert [day.get() for day in dialog.days] == [False] * 5 + [True, False]

    dialog.minute.set("45")
    dialog.save()

    assert app.settings.custom_announcements == (
        WATER,
        CustomAnnouncement("Salon kapanıyor.", datetime.time(9, 45), frozenset({5})),
    )


def test_choosing_days_starts_from_nothing_ticked(
    make_dialog: Callable[[], AnnouncementDialog],
) -> None:
    dialog = make_dialog()

    dialog.every_day.set(False)
    dialog.choose_days()
    assert not any(day.get() for day in dialog.days)

    # Days that were ticked by hand are kept when switching back and forth.
    dialog.days[1].set(True)
    dialog.every_day.set(True)
    dialog.choose_days()
    dialog.every_day.set(False)
    dialog.choose_days()
    assert [day.get() for day in dialog.days] == [False, True] + [False] * 5


def test_dialog_tries_out_the_text_without_saving_it(
    make_dialog: Callable[[], AnnouncementDialog],
    app: App,
    provider: FakeProvider,
    alerts: list[str],
) -> None:
    app.start()
    dialog = make_dialog()

    dialog.test()
    assert alerts == ["Anons metni boş olamaz."]

    dialog.text.set("  Lütfen salonu temiz tutunuz.  ")
    dialog.test()
    wait_for(app, lambda: app.test_status is Status.PLAYED)

    assert [text for text, _ in provider.calls] == ["Lütfen salonu temiz tutunuz."]
    assert app.settings.custom_announcements == ()
    assert dialog.window.winfo_exists()


def test_days_can_be_ticked_only_when_not_every_day(
    make_dialog: Callable[[], AnnouncementDialog],
) -> None:
    dialog = make_dialog()
    assert len(dialog.day_boxes) == 7
    assert all(box.instate(["disabled"]) for box in dialog.day_boxes)

    dialog.every_day.set(False)
    dialog.show_days()

    assert all(box.instate(["!disabled"]) for box in dialog.day_boxes)
