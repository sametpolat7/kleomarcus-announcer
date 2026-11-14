import datetime
import gc
import logging
import queue
import re
import sys
import threading
import time
import tkinter as tk
from collections.abc import Callable, Iterator
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

import pytest

from announcer.app import App, Editing
from announcer.chime import CHIME_PATH
from announcer.models import ALL_WEEKDAYS, CustomAnnouncement, Kind
from announcer.template import DEFAULT_END_TEMPLATE, DEFAULT_TEMPLATE, render
from announcer.today import Row, Status
from announcer.ui import announcement_dialog, change_dialog, main_window, theme
from announcer.ui.announcement_dialog import AnnouncementDialog
from announcer.ui.change_dialog import ChangeDialog
from announcer.ui.instant_dialog import InstantDialog
from announcer.ui.main_window import MainWindow
from support import (
    ROWS,
    FakeClock,
    FakeOutput,
    FakeProvider,
    at,
    wait_for,
    write_schedule,
)

OFF_THE_SCREEN = "+5000+5000"
WATER = CustomAnnouncement("Su içmeyi unutmayın.", datetime.time(19, 0))
CLOSING = CustomAnnouncement("Salon kapanıyor.", datetime.time(9, 30), frozenset({5}))
# Their sessions start at 18:00 on the schedule's Monday, in this order.
AYSE, MEHMET = "Ayşe Yılmaz", "Mehmet Demir"


def welcome(name: str) -> str:
    return render(DEFAULT_TEMPLATE, name)


def goodbye(name: str) -> str:
    return render(DEFAULT_END_TEMPLATE, name)


def start_row(name: str) -> tuple[str, ...]:
    return ("Pazartesi", "18:00", "Başlangıç", welcome(name))


def end_row(name: str) -> tuple[str, ...]:
    return ("Pazartesi", "19:00", "Bitiş", goodbye(name))


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
    monkeypatch.setattr(
        filedialog, "askopenfilename", lambda **options: str(path) if path else ""
    )
    window.choose_button.invoke()


def pick(box: ttk.Combobox, label: str) -> None:
    box.set(label)
    box.event_generate("<<ComboboxSelected>>")


def select(window: MainWindow, row: str) -> None:
    window.today.selection_set(row)
    window.root.update()  # the buttons follow the selection


def statuses(window: MainWindow) -> list[str]:
    return [values[4] for values in today(window)]


def brought_into_view(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(window.today, "see", seen.append)
    return seen


class Holder:
    def __init__(self) -> None:
        self._sounding: queue.Queue[Path] = queue.Queue()
        self._go_on = threading.Semaphore(0)

    def hold(self, path: Path) -> None:
        self._sounding.put(path)
        self._go_on.acquire(timeout=5)

    def next(self) -> Path:
        return self._sounding.get(timeout=5)

    def end(self, files: int = 1) -> None:
        self._go_on.release(files)


@pytest.fixture
def holder(app: App, output: FakeOutput) -> Iterator[Holder]:
    """Each file sounds until the test lets it end. Whatever is still held is
    let go before the app is stopped."""
    made = Holder()
    output.while_playing = made.hold
    yield made
    made.end(10)  # so that nothing is held after a failure


def double_click(window: MainWindow, x: int, y: int) -> None:
    """Two clicks in a row are a double click to tkinter."""
    for _ in range(2):
        window.today.event_generate("<ButtonPress-1>", x=x, y=y)
        window.today.event_generate("<ButtonRelease-1>", x=x, y=y)


def press(widget: tk.Misc, key: str) -> None:
    """Tk hands a key only to the window that has the focus, which one kept off
    the screen never gets, so the binding is run with the fields of a key press
    filled in. A binding that returns "break" ends there, as it ends the key's
    handling in Tk."""
    fields = {"W": str(widget), "K": key.strip("<>"), "A": "{}", "T": "2"}
    script = re.sub(r"%(.)", lambda found: fields.get(found[1], "0"), widget.bind(key))
    widget.tk.eval(f"while 1 {{{script}\nbreak}}")


def focus_on(widget: tk.Misc, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tk gives the focus only to a window on the screen."""
    monkeypatch.setattr(widget.winfo_toplevel(), "focus_get", lambda: widget)


def widgets[Widget: tk.Misc](parent: tk.Misc, kind: type[Widget]) -> list[Widget]:
    found: list[Widget] = []
    for child in parent.winfo_children():
        if isinstance(child, kind):
            found.append(child)
        found += widgets(child, kind)
    return found


def button(parent: tk.Misc, text: str) -> ttk.Button:
    [found] = [
        each for each in widgets(parent, ttk.Button) if each.cget("text") == text
    ]
    return found


def text_entry(parent: tk.Misc) -> ttk.Entry:
    """A spinbox is an entry too."""
    [found] = [
        each for each in widgets(parent, ttk.Entry) if not isinstance(each, ttk.Spinbox)
    ]
    return found


def test_fresh_window_asks_for_a_schedule(window: MainWindow) -> None:
    assert window.schedule_path.get() == ""
    assert window.loaded.get() == "Son Yüklenme Tarihi: —"
    assert window.today_title.get() == "9 Kasım 2026 Pazartesi"
    assert window.time.get() == "08:00:00"
    assert today(window) == [("", "", "", "Bugün için anons yok.", "")]
    assert announcements(window) == [("", "", "Özel anons yok.")]
    assert window.last_announcement.get() == "Son anons: —"
    assert window.template.get() == DEFAULT_TEMPLATE
    assert window.end_template.get() == DEFAULT_END_TEMPLATE
    assert (
        window.speed_box.get(),
        window.delay_box.get(),
        window.end_box.get(),
    ) == ("1.0× (Normal)", "Yok", "Açık")
    assert window.stop_button.instate(["disabled"])
    assert window.instant_button.instate(["!disabled"])
    assert warnings(window).startswith("• Sporcu listesi seçilmedi.")


def test_choosing_a_schedule_shows_todays_announcements(
    window: MainWindow, schedule: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    choose(window, schedule, monkeypatch)

    assert window.schedule_path.get() == str(schedule)
    assert window.loaded.get() == "Son Yüklenme Tarihi: 09.11.2026 08:00:00"
    assert window.today.heading("text", "text") == "Anons"
    assert today(window) == [
        (*start_row(AYSE), "Sıradaki"),
        (*start_row(MEHMET), "Sıradaki"),
        (*end_row(AYSE), "Bekliyor"),
        (*end_row(MEHMET), "Bekliyor"),
    ]
    pump(window, lambda: warnings(window) == "")
    assert not window.warning_box.winfo_manager()


def test_athletes_of_a_minute_have_a_row_each_in_the_order_of_the_schedule(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        ("Zeynep Kaya", "Pazartesi", "18:00"),
        ("Elif Şahin", "Pazartesi", "17:00"),
        ("Ali Kara", "Pazartesi", "18:00"),
    ]

    choose(window, write_schedule(tmp_path / "order.xlsx", rows), monkeypatch)

    # Those whose session ends are said goodbye to before the next are welcomed.
    assert [values[1:4] for values in today(window)] == [
        ("17:00", "Başlangıç", welcome("Elif Şahin")),
        ("18:00", "Bitiş", goodbye("Elif Şahin")),
        ("18:00", "Başlangıç", welcome("Zeynep Kaya")),
        ("18:00", "Başlangıç", welcome("Ali Kara")),
        ("19:00", "Bitiş", goodbye("Zeynep Kaya")),
        ("19:00", "Bitiş", goodbye("Ali Kara")),
    ]


def test_schedule_that_cannot_be_used_gives_an_alert(
    window: MainWindow,
    schedule: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[str],
) -> None:
    choose(window, None, monkeypatch)  # the file dialog is cancelled
    assert (window.schedule_path.get(), alerts) == ("", [])
    choose(window, schedule, monkeypatch)
    broken, notes = tmp_path / "broken.xlsx", tmp_path / "notes.txt"
    broken.write_text("not a workbook")
    notes.write_text("a list of names")

    choose(window, broken, monkeypatch)
    choose(window, notes, monkeypatch)

    assert alerts == [
        "Sporcu listesi okunamadı. Geçerli bir Excel (.xlsx) dosyası olmalıdır.",
        "Bu dosya yüklenemez. Sporcu listesi bir Excel (.xlsx) dosyası olmalıdır.",
    ]
    assert window.schedule_path.get() == str(schedule)
    assert len(today(window)) == 4


def test_saved_schedule_file_is_shown_by_itself(
    window: MainWindow,
    app: App,
    schedule: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.start()
    choose(window, schedule, monkeypatch)
    clock.now = at(9, 15)

    write_schedule(schedule, [*ROWS, ("Elif Şahin", "Pazartesi", "20:30")])

    pump(window, lambda: len(today(window)) == 6)
    assert today(window)[4:] == [
        ("Pazartesi", "20:30", "Başlangıç", welcome("Elif Şahin"), "Bekliyor"),
        ("Pazartesi", "21:30", "Bitiş", goodbye("Elif Şahin"), "Bekliyor"),
    ]
    assert window.loaded.get() == "Son Yüklenme Tarihi: 09.11.2026 09:15:00"


def test_status_follows_the_clock(
    window: MainWindow,
    schedule: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    clock.now = at(18, 0, 20)
    pump(window, lambda: window.time.get() == "18:00:20")
    assert statuses(window) == ["Sıradaki", "Sıradaki", "Bekliyor", "Bekliyor"]

    clock.now = at(18, 0, 31)  # too late for them now
    pump(window, lambda: statuses(window) == ["Saati geçti"] * 2 + ["Sıradaki"] * 2)
    clock.now = at(0, 0) + datetime.timedelta(days=1)
    pump(window, lambda: window.today_title.get() == "10 Kasım 2026 Salı")

    assert today(window) == [
        ("Salı", "20:00", "Başlangıç", welcome("Zeynep Kaya"), "Sıradaki"),
        ("Salı", "21:00", "Bitiş", goodbye("Zeynep Kaya"), "Bekliyor"),
    ]


def test_due_slot_is_shown_as_played(
    window: MainWindow,
    app: App,
    schedule: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    wait_for(app, lambda: not app.generating)

    app.start()
    clock.now = at(18)
    pump(window, lambda: statuses(window)[:2] == ["Okundu"] * 2)

    assert window.last_announcement.get() == (
        "Son anons: 18:00 · Okundu · Başlangıç: Ayşe Yılmaz, Mehmet Demir"
    )


def test_table_follows_the_reading_from_one_athlete_to_the_next(
    window: MainWindow,
    app: App,
    schedule: Path,
    clock: FakeClock,
    holder: Holder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    wait_for(app, lambda: not app.generating)
    app.start()
    clock.now = at(18)

    assert holder.next() == CHIME_PATH
    pump(window, lambda: statuses(window)[:2] == ["Sırada"] * 2)
    holder.end()
    holder.next()
    pump(window, lambda: statuses(window)[:2] == ["Okunuyor", "Sırada"])
    assert window.today.item("0", "tags") == ("PLAYING",)
    holder.end()
    holder.next()
    pump(window, lambda: statuses(window)[:2] == ["Okundu", "Okunuyor"])
    assert window.today.item("0", "tags") == ("PLAYED",)
    assert window.today.item("1", "tags") == ("PLAYING",)
    holder.end()
    pump(window, lambda: statuses(window)[:2] == ["Okundu"] * 2)

    assert statuses(window)[2:] == ["Sıradaki"] * 2
    assert window.last_announcement.get() == (
        "Son anons: 18:00 · Okundu · Başlangıç: Ayşe Yılmaz, Mehmet Demir"
    )


def test_row_being_read_is_brought_into_view_as_the_reading_moves_on(
    window: MainWindow,
    app: App,
    schedule: Path,
    clock: FakeClock,
    holder: Holder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    wait_for(app, lambda: not app.generating)
    window.root.update()
    seen = brought_into_view(window, monkeypatch)
    app.start()
    clock.now = at(18)

    assert holder.next() == CHIME_PATH
    pump(window, lambda: statuses(window)[:2] == ["Sırada"] * 2)
    # The day moved on: the whole minute, its first row last.
    assert seen == ["1", "0"]
    holder.end()
    holder.next()
    pump(window, lambda: statuses(window)[:2] == ["Okunuyor", "Sırada"])
    assert seen == ["1", "0", "0"]
    holder.end()
    holder.next()
    pump(window, lambda: statuses(window)[:2] == ["Okundu", "Okunuyor"])
    assert seen == ["1", "0", "0", "1"]
    holder.end()
    pump(window, lambda: statuses(window)[:2] == ["Okundu"] * 2)

    # The ends were next already while the minute was read, so the table
    # stays where it is until their minute comes.
    assert seen == ["1", "0", "0", "1"]
    assert statuses(window)[2:] == ["Sıradaki"] * 2


def test_replay_waits_until_the_rest_of_the_minute_is_read(
    window: MainWindow,
    app: App,
    schedule: Path,
    output: FakeOutput,
    clock: FakeClock,
    holder: Holder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    wait_for(app, lambda: not app.generating)
    select(window, "0")
    app.start()
    clock.now = at(18)
    assert holder.next() == CHIME_PATH
    holder.end()
    holder.next()
    holder.end()
    holder.next()

    pump(window, lambda: statuses(window)[:2] == ["Okundu", "Okunuyor"])

    assert window.today.selection() == ("0",)
    assert window.replay_button.instate(["disabled"])
    window.replay()  # a press that comes anyway
    assert statuses(window)[:2] == ["Okundu", "Okunuyor"]
    holder.end()
    pump(window, lambda: statuses(window)[:2] == ["Okundu"] * 2)
    assert window.today.selection() == ("0",)
    assert window.replay_button.instate(["!disabled"])
    assert len(output.played) == 3  # the chime and the two sentences, once


def test_rows_that_were_read_stay_read_when_the_template_changes(
    window: MainWindow,
    app: App,
    schedule: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    wait_for(app, lambda: not app.generating)
    app.start()
    clock.now = at(18)
    pump(window, lambda: statuses(window)[:2] == ["Okundu"] * 2)
    clock.now = at(18, 5)

    window.template.set("Hoş geldiniz, {ad_soyad}.")
    window.save_template_button.invoke()

    pump(window, lambda: today(window)[0][3] == "Hoş geldiniz, Ayşe Yılmaz.")
    assert today(window)[:2] == [
        ("Pazartesi", "18:00", "Başlangıç", "Hoş geldiniz, Ayşe Yılmaz.", "Okundu"),
        ("Pazartesi", "18:00", "Başlangıç", "Hoş geldiniz, Mehmet Demir.", "Okundu"),
    ]


def test_table_stays_where_it_is_when_the_template_changes(
    window: MainWindow,
    app: App,
    schedule: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    wait_for(app, lambda: not app.generating)
    window.root.update()
    seen = brought_into_view(window, monkeypatch)

    window.template.set("Hoş geldiniz, {ad_soyad}.")
    window.save_template_button.invoke()
    pump(window, lambda: today(window)[0][3] == "Hoş geldiniz, Ayşe Yılmaz.")

    # The day has not moved on.
    assert statuses(window)[:2] == ["Sıradaki"] * 2
    assert seen == []


def test_replay_needs_a_selected_row_whose_time_has_come(
    window: MainWindow,
    app: App,
    schedule: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The line that says that there are none is not an announcement.
    select(window, "none")
    assert window.replay_button.instate(["disabled"])
    window.replay()

    choose(window, schedule, monkeypatch)
    assert window.replay_button.instate(["disabled"])
    select(window, "0")
    assert window.replay_button.instate(["disabled"])
    window.replay()
    assert today(window)[0][4] == "Sıradaki"

    clock.now = at(18, 1)
    pump(window, lambda: today(window)[0][4] == "Saati geçti")

    assert window.today.selection() == ("0",)
    assert window.replay_button.instate(["!disabled"])


def test_replay_button_announces_the_selected_row_again(
    window: MainWindow,
    app: App,
    schedule: Path,
    output: FakeOutput,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    wait_for(app, lambda: not app.generating)
    app.start()
    clock.now = at(18)
    pump(window, lambda: statuses(window)[:2] == ["Okundu"] * 2)
    chime, ayse, _ = played = list(output.played)
    clock.now = at(18, 4)
    select(window, "0")

    window.replay_button.invoke()

    assert statuses(window)[:2] == ["Sırada", "Okundu"]
    assert window.replay_button.instate(["disabled"])  # one at a time
    pump(window, lambda: today(window)[0][4] == "Okundu")
    assert output.played == [*played, chime, ayse]
    assert window.today.selection() == ("0",)
    assert window.replay_button.instate(["!disabled"])
    assert window.last_announcement.get() == (
        "Son anons: 18:00 · Okundu · Başlangıç: Ayşe Yılmaz"
    )


def test_double_click_and_return_open_the_editor_of_a_row_and_never_read_it(
    window: MainWindow,
    app: App,
    schedule: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[Row, Editing]] = []

    class Editor:
        def __init__(
            self, parent: tk.Misc, app: App, row: Row, editing: Editing
        ) -> None:
            opened.append((row, editing))

        def wait(self) -> None:
            pass

    monkeypatch.setattr(main_window, "ChangeDialog", Editor)
    choose(window, schedule, monkeypatch)
    wait_for(app, lambda: not app.generating)
    if sys.platform != "darwin":
        # Only macOS lays a window out while it is kept off the screen.
        window.root.deiconify()
    pump(window, lambda: bool(window.today.bbox("0")))
    select(window, "0")
    place = window.today.bbox("0")
    assert isinstance(place, tuple)
    left, top, _, height = place

    double_click(window, left + 5, top // 2)  # the headings are above the row
    assert opened == []

    double_click(window, left + 5, top + height // 2)
    press(window.today, "<Return>")
    assert [(row.time, row.kind, row.text) for row, _ in opened] == [
        (datetime.time(18), Kind.START, welcome(AYSE))
    ] * 2
    assert (opened[0][1].name, opened[0][1].planned_text) == (AYSE, welcome(AYSE))

    clock.now = at(18, 5)  # it can be read again now, but no longer changed
    pump(window, lambda: today(window)[0][4] == "Saati geçti")
    double_click(window, left + 5, top + height // 2)
    press(window.today, "<Return>")

    assert len(opened) == 2
    assert today(window)[0][4] == "Saati geçti"


def test_table_stays_where_it_is_while_a_row_is_read_again(
    window: MainWindow,
    app: App,
    schedule: Path,
    output: FakeOutput,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    app.save_announcement(WATER)
    wait_for(app, lambda: not app.generating)
    clock.now = at(18, 5)
    app.start()
    pump(window, lambda: today(window)[0][4] == "Saati geçti")
    seen = brought_into_view(window, monkeypatch)
    select(window, "0")

    window.replay_button.invoke()
    pump(window, lambda: today(window)[0][4] == "Okundu")
    assert seen == []

    output.endless = True  # the chime goes on, so the next ones stay queued
    clock.now = at(19)  # the day moves on to the next announcements
    pump(window, lambda: statuses(window)[2:] == ["Sırada"] * 3)
    # The whole minute: its last row, then its first.
    assert seen == ["4", "2"]


def test_table_brings_the_whole_next_minute_into_view_when_the_day_moves_on(
    window: MainWindow,
    app: App,
    schedule: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    app.save_announcement(WATER)
    window.show()
    window.root.update()
    seen = brought_into_view(window, monkeypatch)

    clock.now = at(18, 0, 31)  # the starts went by

    pump(window, lambda: statuses(window)[2:] == ["Sıradaki"] * 3)
    # Its first row last, so that it is the one that shows if not all fit.
    assert seen == ["4", "2"]
    clock.now = at(18, 30)
    pump(window, lambda: window.time.get() == "18:30:00")
    assert seen == ["4", "2"]


def test_table_follows_the_next_row_when_the_first_athlete_of_its_minute_is_skipped(
    window: MainWindow,
    app: App,
    schedule: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    window.root.update()
    seen = brought_into_view(window, monkeypatch)
    select(window, "0")

    window.skip_button.invoke()

    # Mehmet Demir's row is the next one now, and it is the minute's only one.
    assert statuses(window) == ["Atlanacak", "Sıradaki", "Atlanacak", "Bekliyor"]
    assert seen == ["1", "1"]
    window.skip_button.invoke()
    assert statuses(window) == ["Sıradaki", "Sıradaki", "Bekliyor", "Bekliyor"]
    assert seen == ["1", "1", "1", "0"]


def test_table_follows_the_next_minute_when_a_change_of_the_delay_moves_it(
    window: MainWindow,
    app: App,
    schedule: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    window.root.update()
    seen = brought_into_view(window, monkeypatch)

    pick(window.delay_box, "2 dk")

    assert [values[1] for values in today(window)] == ["18:02"] * 2 + ["19:02"] * 2
    assert statuses(window)[:2] == ["Sıradaki"] * 2
    assert seen == ["1", "0"]


def test_skip_button_skips_the_selected_row_and_takes_that_back(
    window: MainWindow,
    app: App,
    schedule: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The line that says that there are none is not an announcement.
    select(window, "none")
    assert window.skip_button.instate(["disabled"])
    assert window.edit_row_button.instate(["disabled"])
    window.skip()

    choose(window, schedule, monkeypatch)
    select(window, "0")
    assert window.skip_button.instate(["!disabled"])
    assert window.skip_button.cget("text") == "Atla"

    window.skip_button.invoke()

    assert statuses(window) == ["Atlanacak", "Sıradaki", "Atlanacak", "Bekliyor"]
    assert window.today.item("0", "tags") == ("SKIPPING",)
    assert window.today.selection() == ("0",)
    assert window.skip_button.cget("text") == "Atlamayı Geri Al"
    assert window.edit_row_button.instate(["!disabled"])
    assert [(change.kind, change.name, change.skipped) for change in app.changes] == [
        (Kind.START, AYSE, True),
        (Kind.END, AYSE, True),
    ]

    window.skip_button.invoke()

    assert statuses(window) == ["Sıradaki", "Sıradaki", "Bekliyor", "Bekliyor"]
    assert window.skip_button.cget("text") == "Atla"
    assert not app.changes
    clock.now = at(18, 1)
    pump(window, lambda: today(window)[0][4] == "Saati geçti")
    assert window.skip_button.instate(["disabled"])
    assert window.edit_row_button.instate(["disabled"])


def test_skipped_athlete_is_left_out_while_the_other_of_the_minute_is_read(
    window: MainWindow,
    app: App,
    schedule: Path,
    output: FakeOutput,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    wait_for(app, lambda: not app.generating)
    select(window, "0")
    window.skip_button.invoke()
    app.start()

    clock.now = at(18)
    pump(window, lambda: today(window)[1][4] == "Okundu")

    assert statuses(window) == ["Atlanacak", "Okundu", "Atlanacak", "Sıradaki"]
    assert len(output.played) == 2  # the chime and Mehmet Demir's sentence
    assert window.last_announcement.get() == (
        "Son anons: 18:00 · Okundu · Başlangıç: Mehmet Demir"
    )


def test_row_buttons_are_disabled_once_the_row_is_too_close_to_its_minute(
    window: MainWindow,
    app: App,
    schedule: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    wait_for(app, lambda: not app.generating)
    clock.now = at(17, 59, 50)
    pump(window, lambda: window.time.get() == "17:59:50")
    select(window, "0")
    assert window.edit_row_button.instate(["!disabled"])
    assert window.skip_button.instate(["!disabled"])

    clock.now = at(17, 59, 59)
    pump(window, lambda: window.time.get() == "17:59:59")

    assert statuses(window) == ["Sıradaki", "Sıradaki", "Bekliyor", "Bekliyor"]
    assert window.edit_row_button.instate(["disabled"])
    assert window.skip_button.instate(["disabled"])


@pytest.mark.parametrize("pressed", ["edit_row_button", "skip_button"])
def test_row_buttons_pressed_too_close_to_the_minute_tell_that_it_is_too_late(
    window: MainWindow,
    app: App,
    schedule: Path,
    clock: FakeClock,
    alerts: list[str],
    monkeypatch: pytest.MonkeyPatch,
    pressed: str,
) -> None:
    opened: list[tuple[object, ...]] = []
    monkeypatch.setattr(main_window, "ChangeDialog", lambda *args: opened.append(args))
    choose(window, schedule, monkeypatch)
    clock.now = at(17, 59, 50)
    window.show()
    select(window, "0")
    clock.now = at(17, 59, 59)  # before the window looks at the clock again
    stale: ttk.Button = getattr(window, pressed)
    assert stale.instate(["!disabled"])

    stale.invoke()

    assert alerts == ["Bu anonsun saati geldi; artık değiştirilemez."]
    assert opened == []
    assert app.changes == ()
    assert statuses(window) == ["Sıradaki", "Sıradaki", "Bekliyor", "Bekliyor"]
    assert stale.instate(["disabled"])


def test_selection_stays_with_its_row_when_a_start_and_an_end_share_a_minute(
    window: MainWindow,
    app: App,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    twice = [
        ("Ayşe Yılmaz", "Pazartesi", "18:00"),
        ("Ayşe Yılmaz", "Pazartesi", "19:00"),
    ]
    choose(window, write_schedule(tmp_path / "twice.xlsx", twice), monkeypatch)
    assert [values[1:4] for values in today(window)] == [
        ("18:00", "Başlangıç", welcome(AYSE)),
        ("19:00", "Bitiş", goodbye(AYSE)),
        ("19:00", "Başlangıç", welcome(AYSE)),
        ("20:00", "Bitiş", goodbye(AYSE)),
    ]
    select(window, "2")

    window.skip_button.invoke()

    assert window.today.selection() == ("2",)
    assert statuses(window) == ["Sıradaki", "Bekliyor", "Atlanacak", "Atlanacak"]
    assert window.skip_button.cget("text") == "Atlamayı Geri Al"
    select(window, "1")
    assert window.skip_button.cget("text") == "Atla"

    window.skip_button.invoke()

    assert window.today.selection() == ("1",)
    assert statuses(window) == ["Sıradaki", "Atlanacak", "Atlanacak", "Atlanacak"]
    assert window.skip_button.cget("text") == "Atlamayı Geri Al"


def test_edit_button_changes_the_selected_row_for_today_and_keeps_it_selected(
    window: MainWindow,
    app: App,
    schedule: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    app.save_announcement(WATER)
    window.show()
    select(window, "4")

    def move(dialog: ChangeDialog) -> None:
        dialog.window.withdraw()
        dialog.minute.set("30")
        dialog.hour.set("18")
        dialog.save()

    monkeypatch.setattr(ChangeDialog, "wait", move)

    window.edit_row_button.invoke()

    assert today(window) == [
        (*start_row(AYSE), "Sıradaki"),
        (*start_row(MEHMET), "Sıradaki"),
        ("Pazartesi", "18:30", "Özel", WATER.text, "Bekliyor (düzenlendi)"),
        (*end_row(AYSE), "Bekliyor"),
        (*end_row(MEHMET), "Bekliyor"),
    ]
    assert window.today.selection() == ("2",)
    assert app.settings.custom_announcements == (WATER,)


def test_edit_button_moves_one_athlete_and_leaves_the_other_of_the_minute(
    window: MainWindow,
    app: App,
    schedule: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    select(window, "1")

    def move(dialog: ChangeDialog) -> None:
        dialog.window.withdraw()
        dialog.minute.set("30")
        dialog.save()

    monkeypatch.setattr(ChangeDialog, "wait", move)

    window.edit_row_button.invoke()

    assert today(window) == [
        (*start_row(AYSE), "Sıradaki"),
        ("Pazartesi", "18:30", "Başlangıç", welcome(MEHMET), "Bekliyor (düzenlendi)"),
        (*end_row(AYSE), "Bekliyor"),
        (*end_row(MEHMET), "Bekliyor"),
    ]
    assert window.today.selection() == ("1",)
    [change] = app.changes
    assert (change.kind, change.name, change.time) == (
        Kind.START,
        MEHMET,
        datetime.time(18, 30),
    )


def test_stop_button_cuts_off_only_what_is_sounding(
    window: MainWindow,
    app: App,
    schedule: Path,
    output: FakeOutput,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    wait_for(app, lambda: not app.generating)
    app.start()
    window.stop()  # nothing to cut off
    assert window.stop_button.instate(["disabled"])
    # Mehmet Demir's sentence, the second of the minute, goes on until cut off.
    output.while_playing = lambda path: setattr(
        output, "endless", len(output.played) > 1
    )
    clock.now = at(18)
    pump(window, lambda: statuses(window)[:2] == ["Okundu", "Okunuyor"])
    assert app.sounding
    assert window.stop_button.instate(["!disabled"])

    window.stop_button.invoke()

    assert window.stop_button.instate(["disabled"])
    pump(window, lambda: today(window)[1][4] == "Durduruldu")
    assert statuses(window) == ["Okundu", "Durduruldu", "Sıradaki", "Sıradaki"]
    assert not app.sounding
    assert window.last_announcement.get() == (
        "Son anons: 18:00 · Durduruldu · Başlangıç: Ayşe Yılmaz, Mehmet Demir"
    )


def test_instant_button_has_what_is_typed_read_at_once(
    window: MainWindow,
    app: App,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.start()
    clock.now = at(9, 15)

    def type_and_read(dialog: InstantDialog) -> None:
        dialog.window.withdraw()
        dialog.text.set("Salonu boşaltınız.")
        dialog.read()

    monkeypatch.setattr(InstantDialog, "wait", type_and_read)

    window.instant_button.invoke()

    instant = ("Pazartesi", "09:15", "Anlık", "Salonu boşaltınız.")
    assert today(window) == [(*instant, "Hazırlanıyor")]
    pump(window, lambda: today(window) == [(*instant, "Okundu")])
    assert window.last_announcement.get() == (
        "Son anons: 09:15 · Okundu · Anlık: Salonu boşaltınız."
    )


def test_scrolling_over_a_setting_does_not_change_it(window: MainWindow) -> None:
    for box in (window.speed_box, window.delay_box, window.end_box):
        scrolling = [
            sequence
            for sequence in box.bind_class("TCombobox")
            if "Wheel" in sequence or "Scroll" in sequence
        ]
        assert "<MouseWheel>" in scrolling
        assert all(box.bind(sequence) for sequence in scrolling)


def test_todays_table_leaves_out_the_day_while_it_is_narrow(
    window: MainWindow,
) -> None:
    window.today.event_generate("<Configure>", width=300, height=200)

    assert tuple(window.today.cget("displaycolumns")) == (
        "time",
        "kind",
        "text",
        "status",
    )
    window.today.event_generate("<Configure>", width=1200, height=200)
    assert tuple(window.today.cget("displaycolumns")) == (
        "day",
        "time",
        "kind",
        "text",
        "status",
    )


@pytest.mark.parametrize(
    ("table_name", "optional", "wanted"),
    [("today", "day", 320), ("announcements", "days", 160)],
)
def test_table_leaves_out_a_column_before_its_text_gets_narrower_than_wanted(
    window: MainWindow, table_name: str, optional: str, wanted: int
) -> None:
    """Today's table shows whole sentences, so it gives its text more room."""
    table: ttk.Treeview = getattr(window, table_name)
    columns = tuple(table.cget("columns"))
    fixed = sum(int(table.column(name, "width")) for name in columns if name != "text")

    table.event_generate("<Configure>", width=fixed + wanted, height=200)
    assert tuple(table.cget("displaycolumns")) == columns

    table.event_generate("<Configure>", width=fixed + wanted - 1, height=200)
    assert tuple(table.cget("displaycolumns")) == tuple(
        name for name in columns if name != optional
    )


def test_selected_row_stays_selected_when_the_table_changes(
    window: MainWindow, app: App, schedule: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    choose(window, schedule, monkeypatch)
    select(window, "1")

    app.save_announcement(CustomAnnouncement("Günaydın.", datetime.time(9, 0)))
    window.show()

    assert today(window)[2][:4] == start_row(MEHMET)
    assert window.today.selection() == ("2",)


def test_choosing_a_delay_moves_todays_announcements(
    window: MainWindow, app: App, schedule: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    choose(window, schedule, monkeypatch)

    pick(window.delay_box, "2 dk")

    assert app.settings.delay == 2
    assert today(window) == [
        ("Pazartesi", "18:02", "Başlangıç", welcome(AYSE), "Sıradaki"),
        ("Pazartesi", "18:02", "Başlangıç", welcome(MEHMET), "Sıradaki"),
        ("Pazartesi", "19:02", "Bitiş", goodbye(AYSE), "Bekliyor"),
        ("Pazartesi", "19:02", "Bitiş", goodbye(MEHMET), "Bekliyor"),
    ]
    pick(window.delay_box, "Yok")
    assert [values[1] for values in today(window)] == ["18:00"] * 2 + ["19:00"] * 2


def test_end_announcements_can_be_switched_off_and_on(
    window: MainWindow, app: App, schedule: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    choose(window, schedule, monkeypatch)

    pick(window.end_box, "Kapalı")

    assert not app.settings.end_announcements
    assert today(window) == [
        (*start_row(AYSE), "Sıradaki"),
        (*start_row(MEHMET), "Sıradaki"),
    ]
    pick(window.end_box, "Açık")
    assert app.settings.end_announcements
    assert today(window) == [
        (*start_row(AYSE), "Sıradaki"),
        (*start_row(MEHMET), "Sıradaki"),
        (*end_row(AYSE), "Bekliyor"),
        (*end_row(MEHMET), "Bekliyor"),
    ]


def test_choosing_a_speed_generates_the_speech_at_it(
    window: MainWindow,
    app: App,
    schedule: Path,
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choose(window, schedule, monkeypatch)
    wait_for(app, lambda: not app.generating)

    pick(window.speed_box, "0.9×")

    assert app.settings.speed == 0.9
    assert window.activity.get() == "Sesler oluşturuluyor…"
    pump(window, lambda: window.activity.get() == "")
    assert app.spoken_speed == 0.9
    assert provider.speeds[-3:] == [0.9] * 3
    assert window.speed_box.get() == "0.9×"


def test_settings_show_what_is_stored_and_stay_when_they_cannot_be_saved(
    window: MainWindow, app: App, settings_file: Path, alerts: list[str]
) -> None:
    app.set_speed(1.1)
    app.set_delay(5)
    app.set_end_announcements(False)
    window.show()
    boxes = (window.speed_box, window.delay_box, window.end_box)
    assert [box.get() for box in boxes] == ["1.1×", "5 dk", "Kapalı"]
    settings_file.unlink()
    settings_file.mkdir()

    pick(window.speed_box, "0.8×")
    pick(window.delay_box, "10 dk")
    pick(window.end_box, "Açık")

    assert alerts == ["Değişiklik kaydedilemedi. Ayrıntılar kayıt dosyasındadır."] * 3
    assert [box.get() for box in boxes] == ["1.1×", "5 dk", "Kapalı"]
    settings = app.settings
    assert (settings.speed, settings.delay, settings.end_announcements) == (
        1.1,
        5,
        False,
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

    # Three athletes, each with a sentence at the start and one at the end.
    assert warnings(window).startswith("• 6 anonsun sesi oluşturulamadı.")


def test_warnings_show_in_a_box_that_grows_to_three_lines(
    window: MainWindow, app: App, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert window.warning_box.winfo_manager() == "grid"
    assert window.warnings.cget("height") == 1

    typos = [("", "Funday", "akşam")] * 6
    choose(window, write_schedule(tmp_path / "typos.xlsx", typos), monkeypatch)

    assert warnings(window).count("satır atlandı") == 6
    assert window.warnings.cget("height") == 3


def test_template_can_be_saved_only_while_it_is_changed(
    window: MainWindow, app: App
) -> None:
    assert window.save_template_button.instate(["disabled"])

    window.template.set("  Hoş geldiniz, {ad_soyad}.  ")
    assert window.save_template_button.instate(["!disabled"])
    window.template.set(DEFAULT_TEMPLATE)
    window.end_template.set("  Güle güle, {ad_soyad}.  ")
    assert window.save_template_button.instate(["!disabled"])
    window.template.set("  Hoş geldiniz, {ad_soyad}.  ")

    window.save_template_button.invoke()
    assert app.settings.template == "Hoş geldiniz, {ad_soyad}."
    assert app.settings.end_template == "Güle güle, {ad_soyad}."
    assert window.save_template_button.instate(["disabled"])

    window.template.set(f"  {app.settings.template}  ")
    window.end_template.set(f"{app.settings.end_template}  ")
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


def test_edit_and_delete_need_a_selected_announcement(
    window: MainWindow, app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        AnnouncementDialog, "wait", lambda dialog: pytest.fail("opened")
    )
    # The line that says that there are none is not an announcement.
    window.announcements.selection_set("none")
    window.root.update()
    assert window.edit_button.instate(["disabled"])
    assert window.delete_button.instate(["disabled"])
    window.edit()
    window.delete()

    app.save_announcement(WATER)
    window.show()
    assert window.edit_button.instate(["disabled"])
    window.announcements.selection_set("0")
    window.root.update()

    assert window.edit_button.instate(["!disabled"])
    assert window.delete_button.instate(["!disabled"])


def test_template_without_the_placeholder_gives_an_alert(
    window: MainWindow, app: App, alerts: list[str]
) -> None:
    window.template.set("Hoş geldiniz.")
    window.end_template.set("Güle güle.")

    window.save_template_button.invoke()
    window.test_button.invoke()
    window.end_test_button.invoke()

    assert alerts == [
        "Başlangıç şablonunda “{ad_soyad}” yer tutucusu bulunmalıdır.",
        "Şablonda “{ad_soyad}” yer tutucusu bulunmalıdır.",
        "Şablonda “{ad_soyad}” yer tutucusu bulunmalıdır.",
    ]
    assert app.settings.template == DEFAULT_TEMPLATE
    assert app.test_status is None
    assert window.template.get() == "Hoş geldiniz."


@pytest.mark.parametrize(
    ("template", "end_template", "message"),
    [
        (
            "Hoş geldiniz.",
            "Güle güle, {ad_soyad}.",
            "Başlangıç şablonunda “{ad_soyad}” yer tutucusu bulunmalıdır.",
        ),
        (
            "Hoş geldiniz, {ad_soyad}.",
            "Güle güle.",
            "Bitiş şablonunda “{ad_soyad}” yer tutucusu bulunmalıdır.",
        ),
    ],
)
def test_saving_the_templates_names_the_one_without_the_placeholder(
    window: MainWindow,
    app: App,
    alerts: list[str],
    template: str,
    end_template: str,
    message: str,
) -> None:
    window.template.set(template)
    window.end_template.set(end_template)

    window.save_template_button.invoke()

    assert alerts == [message]
    # Both stay as typed, to be put right.
    assert (app.settings.template, app.settings.end_template) == (
        DEFAULT_TEMPLATE,
        DEFAULT_END_TEMPLATE,
    )
    assert (window.template.get(), window.end_template.get()) == (
        template,
        end_template,
    )
    assert window.save_template_button.instate(["!disabled"])


def test_test_button_announces_what_is_typed_without_saving_it(
    window: MainWindow, app: App, provider: FakeProvider
) -> None:
    app.start()
    window.template.set("Merhaba {ad_soyad}.")

    window.test_button.invoke()
    assert window.activity.get() == "Test anonsu hazırlanıyor…"
    pump(window, lambda: window.activity.get() == "Test anonsu okundu.")
    window.end_template.set("Güle güle {ad_soyad}.")
    window.end_test_button.invoke()
    assert window.activity.get() == "Test anonsu hazırlanıyor…"
    pump(window, lambda: window.activity.get() == "Test anonsu okundu.")

    assert [text for text, _ in provider.calls] == [
        "Merhaba Ayşe Yılmaz.",
        "Güle güle Ayşe Yılmaz.",
    ]
    assert app.settings.template == DEFAULT_TEMPLATE
    assert app.settings.end_template == DEFAULT_END_TEMPLATE
    assert window.template.get() == "Merhaba {ad_soyad}."


def test_custom_announcements_are_listed_by_time(window: MainWindow, app: App) -> None:
    app.save_announcement(WATER)
    app.save_announcement(CLOSING)

    window.show()

    assert announcements(window) == [
        ("09:30", "Cumartesi", "Salon kapanıyor."),
        ("19:00", "Her gün", "Su içmeyi unutmayın."),
    ]
    window.announcements.focus("1")
    window.show()
    assert window.announcements.focus() == "1"


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


def test_log_button_opens_the_log_file_if_there_is_one(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[str],
) -> None:
    opened: list[Path] = []
    monkeypatch.setattr(main_window, "open_file", opened.append)

    window.log_button.invoke()
    assert (opened, alerts) == ([], ["Kayıt dosyası bulunamadı."])

    (tmp_path / "announcer.log").write_text("a line")
    window.log_button.invoke()
    assert opened == [tmp_path / "announcer.log"]


def test_window_can_be_closed_while_the_dialog_is_open(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, alerts: list[str]
) -> None:
    # Closing the main window ends the dialog's wait.
    monkeypatch.setattr(AnnouncementDialog, "wait", lambda dialog: window.close())

    window.add()

    assert window.closed
    assert alerts == []


def test_error_in_an_action_is_logged_and_told(
    window: MainWindow,
    app: App,
    alerts: list[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail(template: str) -> None:
        raise RuntimeError("the test broke")

    monkeypatch.setattr(app, "test", fail)

    with caplog.at_level(logging.ERROR):
        window.test_button.invoke()

    assert alerts == ["Beklenmeyen bir hata oluştu. Ayrıntılar kayıt dosyasındadır."]
    assert "An action in the window failed" in caplog.text
    assert "RuntimeError: the test broke" in caplog.text


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
        pump(window, lambda: window.activity.get() == "")

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


def test_dialog_adds_an_announcement_and_closes(
    make_dialog: Callable[[], AnnouncementDialog], app: App
) -> None:
    dialog = make_dialog()
    assert (dialog.text.get(), dialog.hour.get(), dialog.minute.get()) == (
        "",
        "12",
        "00",
    )
    assert dialog.every_day.get()
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
        ("Duyuru", "on", "", {0}, "Saat 00:00 ile 23:59 arasında olmalıdır."),
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
    assert all(box.instate(["disabled"]) for box in dialog.day_boxes)

    dialog.every_day.set(False)
    dialog.choose_days()
    assert all(box.instate(["!disabled"]) for box in dialog.day_boxes)
    assert not any(day.get() for day in dialog.days)

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
    assert dialog.test_status.get() == "Test anonsu hazırlanıyor…"
    assert dialog.test_button.instate(["disabled"])
    wait_for(app, lambda: app.test_status is Status.PLAYED)

    assert [text for text, _ in provider.calls] == ["Lütfen salonu temiz tutunuz."]
    assert app.settings.custom_announcements == ()
    assert dialog.window.winfo_exists()


def test_dialog_shows_how_the_test_went(
    make_dialog: Callable[[], AnnouncementDialog],
    window: MainWindow,
    app: App,
    provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(announcement_dialog, "_FOLLOW", 5)
    provider.offline = True
    app.start()
    dialog = make_dialog()
    assert dialog.test_status.get() == ""

    dialog.text.set("Lütfen salonu temiz tutunuz.")
    dialog.test()
    pump(window, lambda: dialog.test_status.get().startswith("Test anonsunun sesi"))

    assert dialog.test_button.instate(["!disabled"])


def test_return_in_the_dialog_presses_the_button_it_is_on_and_saves_elsewhere(
    make_dialog: Callable[[], AnnouncementDialog],
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = make_dialog()
    cancelled.text.set("Lütfen salonu temiz tutunuz.")
    focus_on(button(cancelled.window, "Vazgeç"), monkeypatch)

    press(cancelled.window, "<Return>")

    assert not cancelled.window.winfo_exists()
    assert not app.settings.custom_announcements
    dialog = make_dialog()
    dialog.text.set("Lütfen salonu temiz tutunuz.")
    focus_on(text_entry(dialog.window), monkeypatch)
    press(dialog.window, "<Return>")
    assert not dialog.window.winfo_exists()
    assert app.settings.custom_announcements == (
        CustomAnnouncement("Lütfen salonu temiz tutunuz.", datetime.time(12, 0)),
    )


@pytest.fixture
def low_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    """The screen of a small laptop, for a window that is opened after this."""
    monkeypatch.setattr(tk.Misc, "winfo_screenwidth", lambda self: 1024)
    monkeypatch.setattr(tk.Misc, "winfo_screenheight", lambda self: 768)


def test_window_starts_where_all_of_it_is_on_a_low_screen(
    low_screen: None, window: MainWindow
) -> None:
    window.root.update_idletasks()

    # What the window returns to from being maximized.
    size, left, top = window.root.geometry().split("+")

    assert (size, left) == ("944x648", "40")  # in the middle, from side to side
    # 80 pixels are room enough for the title bar and the taskbar of Windows.
    # macOS moves the window down a little, to below its menu bar.
    assert int(top) + 648 + 80 <= 768


def test_wrapped_warnings_get_the_lines_they_need(
    window: MainWindow, app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    long = "Bu uyarı pencereden çok daha geniş olduğu için birkaç satıra yayılır. " * 4
    monkeypatch.setattr("announcer.ui.texts.warnings", lambda app: [long, "Kısa."])
    if sys.platform != "darwin":
        # Only macOS lays a window out while it is kept off the screen.
        window.root.deiconify()
    window.root.geometry("720x680")

    window.show()
    pump(window, lambda: int(window.warnings.cget("height")) >= 3)

    last_line_in_view = window.warnings.bbox("end-1c") is not None
    assert last_line_in_view or window.warnings.yview() != (0.0, 1.0)


@pytest.mark.parametrize("table_name", ["today", "announcements"])
def test_table_has_a_scrollbar_only_while_not_all_of_its_rows_fit(
    window: MainWindow, table_name: str
) -> None:
    table: ttk.Treeview = getattr(window, table_name)
    [scrollbar] = widgets(table.master, ttk.Scrollbar)
    scrolled = str(table.cget("yscrollcommand"))

    table.tk.call(scrolled, 0.0, 0.5)
    assert scrollbar.winfo_manager() == "grid"
    assert scrollbar.get() == (0.0, 0.5)

    table.tk.call(scrolled, 0.0, 1.0)
    assert not scrollbar.winfo_manager()

    table.tk.call(scrolled, 0.25, 1.0)
    assert scrollbar.winfo_manager() == "grid"


def test_window_and_its_dialogs_have_the_same_look_on_every_system(
    window: MainWindow, make_instant_dialog: Callable[[], InstantDialog]
) -> None:
    style = ttk.Style(window.root)
    assert style.theme_use() == "clam"
    assert tk.TkVersion >= 9

    assert window.root.cget("background") == theme.WINDOW
    dialog = make_instant_dialog()
    assert dialog.window.cget("background") == theme.CARD
    assert style.lookup("TFrame", "background") == theme.CARD
    assert str(window.instant_button.cget("style")) == "Primary.TButton"
    assert str(dialog.read_button.cget("style")) == "Primary.TButton"
    assert style.lookup("Primary.TButton", "background") == theme.INK


def test_no_colour_of_the_base_theme_is_left_in_any_state(window: MainWindow) -> None:
    style = ttk.Style(window.root)
    palette = {
        value
        for value in vars(theme).values()
        if isinstance(value, str) and value.startswith("#")
    }
    restyled = (
        ".",
        "TButton",
        "Primary.TButton",
        "Danger.TButton",
        "TEntry",
        "Shown.TEntry",
        "TCombobox",
        "TSpinbox",
        "TCheckbutton",
        "TRadiobutton",
        "Treeview",
        "Treeview.Heading",
        "Vertical.TScrollbar",
    )

    for name in restyled:
        for option, colours in style.map(name).items():
            assert {str(colour[-1]) for colour in colours} <= palette, (name, option)
    for field in ("TEntry", "TCombobox", "TSpinbox"):
        for state in ([], ["focus"], ["readonly"], ["readonly", "focus"], ["disabled"]):
            text = style.lookup(field, "foreground", state)
            assert text != style.lookup(field, "fieldbackground", state), (field, state)
            assert text == (theme.TEXT if "disabled" not in state else "#a3a9b3")


def test_row_keeps_the_colour_of_its_status_while_it_is_selected(
    window: MainWindow,
) -> None:
    style = ttk.Style(window.root)

    assert not style.map("Treeview", "foreground")
    assert style.map("Treeview", "background")


def test_arrows_of_a_time_box_are_easy_to_hit(window: MainWindow) -> None:
    shown = tk.Toplevel(window.root)
    shown.geometry(OFF_THE_SCREEN)
    box = ttk.Spinbox(shown, from_=0, to=23, width=3)
    box.pack()
    pump(window, lambda: bool(box.identify(1, 1)))

    rows: dict[str, int] = {}
    for y in range(box.winfo_height()):
        part = str(box.identify(box.winfo_width() - 8, y))
        rows[part] = rows.get(part, 0) + 1

    assert rows["Plain.Spinbox.uparrow"] >= 10
    assert rows["Plain.Spinbox.downarrow"] >= 10


def test_pointer_follows_a_button_that_a_press_disables_or_closes(
    window: MainWindow, make_instant_dialog: Callable[[], InstantDialog]
) -> None:
    errors: list[str] = []
    window.root.tk.createcommand("bgerror", errors.append)
    stop = window.stop_button
    assert stop.instate(["disabled"])
    stop.configure(cursor=theme.POINTER)

    stop.event_generate("<ButtonRelease-1>", x=2, y=2)
    assert not str(stop.cget("cursor"))

    dialog = make_instant_dialog()
    cancel = button(dialog.window, "Vazgeç")
    cancel.state(["pressed"])
    cancel.event_generate("<ButtonRelease-1>", x=2, y=2)
    window.root.update()
    assert not dialog.window.winfo_exists()
    assert errors == []


@pytest.mark.skipif(sys.platform != "darwin", reason="only macOS has a dark look")
def test_window_and_dialogs_stay_light_where_the_system_is_dark(
    window: MainWindow, make_instant_dialog: Callable[[], InstantDialog]
) -> None:
    for shown in (window.root, make_instant_dialog().window):
        assert shown.tk.call("wm", "attributes", shown, "-appearance") == "aqua"


def test_pointer_is_a_hand_over_what_can_be_pressed(window: MainWindow) -> None:
    pressable, disabled = window.instant_button, window.replay_button
    assert disabled.instate(["disabled"])

    for button in (pressable, disabled):
        button.event_generate("<Motion>", x=2, y=2)

    assert str(pressable.cget("cursor")) == theme.POINTER
    assert not str(disabled.cget("cursor"))
    for box in (window.speed_box, window.delay_box, window.end_box):
        assert str(box.cget("cursor")) == theme.POINTER


@pytest.mark.skipif(
    sys.platform == "darwin", reason="macOS lists the choices in a menu of its own"
)
def test_choice_drops_its_list_at_one_click_and_keeps_its_text_when_it_is_closed(
    window: MainWindow, app: App
) -> None:
    window.root.deiconify()
    box = window.delay_box
    popdown = str(box.tk.call("ttk::combobox::PopdownWindow", box))
    choices = f"{popdown}.f.l"

    def dropped_after(*event: str) -> bool:
        box.tk.call("event", "generate", *event)
        focus_has_settled = time.monotonic() + 0.5
        pump(window, lambda: time.monotonic() > focus_has_settled)
        return bool(box.tk.call("winfo", "ismapped", popdown))

    click = (str(box), "<Button-1>", "-x", "5", "-y", "5")
    assert dropped_after(*click)
    assert not dropped_after(popdown, "<Button-1>")
    assert box.get() == "Yok"
    assert box.instate(["!pressed"])

    assert dropped_after(*click)
    box.tk.call(choices, "selection", "clear", 0, "end")
    box.tk.call(choices, "selection", "set", 2)
    assert not dropped_after(choices, "<ButtonRelease-1>")
    assert box.get() == "2 dk"
    assert app.settings.delay == 2
    assert box.instate(["!pressed"])

    assert dropped_after(*click)


def test_choosing_a_schedule_stands_out_until_one_is_chosen(
    window: MainWindow, schedule: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert str(window.choose_button.cget("style")) == "Primary.TButton"

    choose(window, schedule, monkeypatch)

    assert str(window.choose_button.cget("style")) == "TButton"


def test_fonts_of_the_look_follow_the_font_of_the_system(window: MainWindow) -> None:
    base = font.nametofont("TkDefaultFont", window.root)
    family, size = str(base.cget("family")), int(base.cget("size"))
    try:
        base.configure(size=13)
        assert theme.describe(window.root) == (family, 13)
        assert theme.describe(window.root, 2, "bold") == (family, 15, "bold")
        base.configure(size=-12)
        assert theme.describe(window.root, 2) == (family, -14)
        assert theme.describe(window.root, -1, "overstrike") == (
            family,
            -11,
            "overstrike",
        )
    finally:
        base.configure(size=size)


@pytest.mark.parametrize(("left", "top"), [(100, 50), (-1900, -300)])
def test_dialog_opens_over_the_window_a_little_above_its_middle(
    window: MainWindow,
    make_instant_dialog: Callable[[], InstantDialog],
    monkeypatch: pytest.MonkeyPatch,
    left: int,
    top: int,
) -> None:
    dialog = make_instant_dialog()
    steps: list[str] = []
    for step in ("deiconify", "wait_visibility", "grab_set", "wait_window"):
        monkeypatch.setattr(
            dialog.window, step, lambda *args, step=step: steps.append(step)
        )
    for name, value in (
        ("winfo_rootx", left),
        ("winfo_rooty", top),
        ("winfo_width", 1200),
        ("winfo_height", 800),
    ):
        monkeypatch.setattr(window.root, name, lambda value=value: value)

    dialog.wait()

    assert steps == ["deiconify", "wait_visibility", "grab_set", "wait_window"]
    width, height = dialog.window.winfo_reqwidth(), dialog.window.winfo_reqheight()
    assert 1 < width < 1200
    assert 1 < height < 800
    placed = dialog.window.geometry().split("+")[1:]
    assert [int(coordinate) for coordinate in placed] == [
        left + (1200 - width) // 2,
        top + (800 - height) // 3,
    ]


def test_window_opens_maximized_and_stays_usable_where_it_cannot_be(
    window: MainWindow,
    alerts: list[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    states: list[str] = []
    # Neither really happens, so that nothing comes up on the screen.
    monkeypatch.setattr(window.root, "state", states.append)
    monkeypatch.setattr(window.root, "mainloop", lambda n=0: None)

    window.run()
    assert states == ["zoomed"]

    def refuse(state: str) -> None:
        raise tk.TclError(f'bad argument "{state}"')

    monkeypatch.setattr(window.root, "state", refuse)
    with caplog.at_level(logging.ERROR):
        window.maximize()

    assert "Cannot maximize the window" in caplog.text
    assert alerts == []


@pytest.fixture
def attributes(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> dict[str, object]:
    """The window's attributes, kept here so that nothing comes up on the screen."""
    kept: dict[str, object] = {"-fullscreen": 0}

    def wm_attributes(name: str, *value: object) -> object:
        if value:
            kept[name] = value[0]
            return ""
        return kept[name]

    monkeypatch.setattr(window.root, "attributes", wm_attributes)
    return kept


def test_full_screen_is_toggled_and_left_with_escape(
    window: MainWindow, attributes: dict[str, object]
) -> None:
    window.leave_full_screen()
    assert not attributes["-fullscreen"]
    window.toggle_full_screen()
    assert attributes["-fullscreen"]
    window.toggle_full_screen()
    assert not attributes["-fullscreen"]

    press(window.root, "<F11>")
    assert attributes["-fullscreen"]
    press(window.root, "<Escape>")
    assert not attributes["-fullscreen"]
    press(window.root, "<Escape>")
    assert not attributes["-fullscreen"]


@pytest.mark.skipif(sys.platform != "darwin", reason="these are macOS's own keys")
def test_full_screen_is_toggled_with_the_keys_of_macos(
    window: MainWindow, attributes: dict[str, object]
) -> None:
    press(window.root, "<Control-Command-f>")
    assert attributes["-fullscreen"]
    press(window.root, "<Control-Command-F>")  # with Shift held as well
    assert not attributes["-fullscreen"]


@pytest.mark.skipif(sys.platform != "darwin", reason="Quit in the menu is macOS's")
def test_quit_in_the_macos_menu_asks_first_too(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions: list[str] = []

    def askyesno(title: str, message: str, **options: object) -> bool:
        questions.append(message)
        return False

    monkeypatch.setattr(messagebox, "askyesno", askyesno)

    window.root.tk.call("::tk::mac::Quit")

    assert len(questions) == 1
    assert not window.closed


@pytest.fixture
def make_change_dialog(
    window: MainWindow, app: App, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[int], ChangeDialog]]:
    """Open the editor of today's row with the index over the window; it is
    closed after the test."""
    monkeypatch.setattr(change_dialog, "_FOLLOW", 5)
    dialogs: list[ChangeDialog] = []

    def make(index: int) -> ChangeDialog:
        row = app.rows()[index]
        editing = app.editing(row)
        assert editing is not None
        dialog = ChangeDialog(window.root, app, row, editing)
        dialog.window.withdraw()
        dialogs.append(dialog)
        return dialog

    yield make
    for dialog in dialogs:
        if dialog.window.winfo_exists():
            dialog.window.destroy()


def labels(dialog: ChangeDialog) -> list[str]:
    return [str(label.cget("text")) for label in widgets(dialog.window, ttk.Label)]


def heading(dialog: ChangeDialog) -> str:
    [found] = [text for text in labels(dialog) if "planlanan saat" in text]
    return found


def held(dialog: ChangeDialog) -> list[ttk.Widget]:
    """What the operator fills in and presses, but for Vazgeç."""
    spinboxes = widgets(dialog.window, ttk.Spinbox)
    assert len(spinboxes) == 2
    return [
        *spinboxes,
        text_entry(dialog.window),
        dialog.test_button,
        dialog.reset_button,
        dialog.save_button,
    ]


def test_change_dialog_fills_in_the_plan_again_without_saving_it(
    make_change_dialog: Callable[[int], ChangeDialog], app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    wait_for(app, lambda: not app.generating)
    speech = app.make_speech(["Merhaba Ayşe Yılmaz."])
    wait_for(app, lambda: speech.done)
    app.edit(app.rows()[0], datetime.time(18, 30), "Merhaba Ayşe Yılmaz.")
    changes = app.changes
    dialog = make_change_dialog(1)  # after Mehmet Demir's, which is at 18:00
    assert heading(dialog) == "Başlangıç anonsu · Ayşe Yılmaz · planlanan saat 18:00"
    assert (dialog.hour.get(), dialog.minute.get()) == ("18", "30")
    assert dialog.text.get() == "Merhaba Ayşe Yılmaz."
    assert (
        "Bu sporcunun 19:00 bitiş anonsu bu değişiklikten etkilenmez; "
        "gerekirse onu da ayrıca düzenleyin."
    ) in labels(dialog)
    dialog.text.set("Selam {ad_soyad}.")

    dialog.reset()

    assert (dialog.hour.get(), dialog.minute.get()) == ("18", "00")
    assert dialog.text.get() == welcome(AYSE)
    assert app.changes == changes
    assert dialog.window.winfo_exists()


def test_change_dialog_of_an_end_shows_the_end_sentence_and_fills_it_in_again(
    make_change_dialog: Callable[[int], ChangeDialog], app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    app.set_templates(DEFAULT_TEMPLATE, "Güle güle, {ad_soyad}.")
    wait_for(app, lambda: not app.generating)
    dialog = make_change_dialog(3)
    assert heading(dialog) == "Bitiş anonsu · Mehmet Demir · planlanan saat 19:00"
    assert (dialog.hour.get(), dialog.minute.get()) == ("19", "00")
    assert dialog.text.get() == "Güle güle, Mehmet Demir."
    # Only the start of a session has an end to tell about.
    assert not [text for text in labels(dialog) if "etkilenmez" in text]
    dialog.minute.set("30")
    dialog.text.set("Hoşça kalın, {ad_soyad}.")

    dialog.reset()

    assert (dialog.hour.get(), dialog.minute.get()) == ("19", "00")
    assert dialog.text.get() == "Güle güle, Mehmet Demir."
    assert not app.changes


def test_change_dialog_saves_a_new_text_once_its_speech_is_made(
    make_change_dialog: Callable[[int], ChangeDialog],
    window: MainWindow,
    app: App,
    schedule: Path,
    provider: FakeProvider,
) -> None:
    app.choose_schedule(schedule)
    wait_for(app, lambda: not app.generating)
    dialog = make_change_dialog(0)
    dialog.text.set("  Hoş geldiniz, {ad_soyad}.  ")

    dialog.save()

    assert dialog.test_status.get() == "Yeni metnin sesi hazırlanıyor…"
    assert dialog.save_button.instate(["disabled"])
    assert not app.changes
    pump(window, lambda: not dialog.window.winfo_exists())
    said = {text for text, _ in provider.calls}
    assert "Hoş geldiniz, Ayşe Yılmaz." in said
    assert "Hoş geldiniz, Mehmet Demir." not in said
    [change] = app.changes
    assert (change.kind, change.name, change.text) == (
        Kind.START,
        AYSE,
        "Hoş geldiniz, Ayşe Yılmaz.",
    )
    window.show()
    assert today(window) == [
        (
            "Pazartesi",
            "18:00",
            "Başlangıç",
            "Hoş geldiniz, Ayşe Yılmaz.",
            "Sıradaki (düzenlendi)",
        ),
        (*start_row(MEHMET), "Sıradaki"),
        (*end_row(AYSE), "Bekliyor"),
        (*end_row(MEHMET), "Bekliyor"),
    ]


def test_change_dialog_takes_a_sentence_without_the_placeholder(
    make_change_dialog: Callable[[int], ChangeDialog],
    window: MainWindow,
    app: App,
    schedule: Path,
    alerts: list[str],
) -> None:
    app.choose_schedule(schedule)
    wait_for(app, lambda: not app.generating)
    dialog = make_change_dialog(1)
    dialog.text.set("Mehmet Bey, hoş geldiniz.")

    dialog.save()

    pump(window, lambda: not dialog.window.winfo_exists())
    assert alerts == []
    [change] = app.changes
    assert (change.name, change.text) == (MEHMET, "Mehmet Bey, hoş geldiniz.")
    window.show()
    assert [values[3:] for values in today(window)[:2]] == [
        (welcome(AYSE), "Sıradaki"),
        ("Mehmet Bey, hoş geldiniz.", "Sıradaki (düzenlendi)"),
    ]


def test_change_dialog_saves_nothing_for_a_text_that_reads_as_planned(
    make_change_dialog: Callable[[int], ChangeDialog],
    window: MainWindow,
    app: App,
    schedule: Path,
) -> None:
    app.choose_schedule(schedule)
    wait_for(app, lambda: not app.generating)
    dialog = make_change_dialog(0)
    # With Ayşe Yılmaz's name in it, the template is her planned sentence.
    dialog.text.set(DEFAULT_TEMPLATE)

    dialog.save()

    pump(window, lambda: not dialog.window.winfo_exists())
    assert not app.changes
    window.show()
    assert today(window)[0] == (*start_row(AYSE), "Sıradaki")


def test_change_dialog_rewords_a_custom_announcement_for_today_only(
    make_change_dialog: Callable[[int], ChangeDialog],
    window: MainWindow,
    app: App,
    provider: FakeProvider,
) -> None:
    app.save_announcement(WATER)
    wait_for(app, lambda: not app.generating)
    dialog = make_change_dialog(0)
    assert heading(dialog) == "Özel anons · planlanan saat 19:00"
    assert dialog.text.get() == WATER.text
    dialog.text.set("  Bol su içiniz.  ")

    dialog.save()

    assert dialog.test_status.get() == "Yeni metnin sesi hazırlanıyor…"
    pump(window, lambda: not dialog.window.winfo_exists())
    assert "Bol su içiniz." in {text for text, _ in provider.calls}
    [change] = app.changes
    assert (change.kind, change.time, change.text) == (
        Kind.CUSTOM,
        None,
        "Bol su içiniz.",
    )
    assert app.settings.custom_announcements == (WATER,)
    window.show()
    assert today(window) == [
        ("Pazartesi", "19:00", "Özel", "Bol su içiniz.", "Sıradaki (düzenlendi)")
    ]


def test_change_dialog_stays_open_when_the_new_text_has_no_speech(
    make_change_dialog: Callable[[int], ChangeDialog],
    window: MainWindow,
    app: App,
    schedule: Path,
    provider: FakeProvider,
    alerts: list[str],
) -> None:
    app.choose_schedule(schedule)
    wait_for(app, lambda: not app.generating)
    provider.offline = True
    dialog = make_change_dialog(0)
    dialog.text.set("Hoş geldiniz, {ad_soyad}.")

    dialog.save()
    pump(window, lambda: bool(alerts))

    assert alerts == [
        "Yeni metnin sesi oluşturulamadı. İnternet bağlantısını kontrol edin. "
        "Saat, metin plana döndürülerek yine de değiştirilebilir."
    ]
    assert dialog.window.winfo_exists()
    assert dialog.save_button.instate(["!disabled"])
    assert dialog.test_status.get() == ""
    assert not app.changes

    # The time can still be changed, with the text of the plan.
    dialog.reset()
    dialog.minute.set("30")
    dialog.save()

    assert not dialog.window.winfo_exists()
    [change] = app.changes
    assert (change.time, change.text) == (datetime.time(18, 30), None)


def test_change_dialog_holds_what_is_entered_while_the_new_texts_speech_is_made(
    make_change_dialog: Callable[[int], ChangeDialog],
    window: MainWindow,
    app: App,
    schedule: Path,
    provider: FakeProvider,
    alerts: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.choose_schedule(schedule)
    wait_for(app, lambda: not app.generating)
    released = threading.Event()
    synthesize = provider.synthesize

    def slow(text: str, voice: str, speed: float) -> bytes:
        released.wait(5)
        return synthesize(text, voice, speed)

    monkeypatch.setattr(provider, "synthesize", slow)
    dialog = make_change_dialog(0)
    inputs = held(dialog)
    assert all(widget.instate(["!disabled"]) for widget in inputs)
    dialog.text.set("Hoş geldiniz, {ad_soyad}.")

    try:
        dialog.save()
        window.root.update()
        assert all(widget.instate(["disabled"]) for widget in inputs)
        provider.offline = True  # so that the dialog stays open afterwards
    finally:
        released.set()
    pump(window, lambda: bool(alerts))

    assert dialog.window.winfo_exists()
    assert all(widget.instate(["!disabled"]) for widget in inputs)
    assert dialog.text.get() == "Hoş geldiniz, {ad_soyad}."
    assert not app.changes


def test_change_dialog_closes_once_its_row_can_no_longer_be_changed(
    make_change_dialog: Callable[[int], ChangeDialog],
    app: App,
    schedule: Path,
    clock: FakeClock,
    alerts: list[str],
) -> None:
    app.choose_schedule(schedule)
    dialog = make_change_dialog(0)
    dialog.minute.set("30")
    clock.now = at(17, 59, 59)  # too close to its minute for a change

    dialog.save()

    assert alerts == ["Bu anonsun saati geldi; artık değiştirilemez."]
    assert not dialog.window.winfo_exists()
    assert not app.changes


@pytest.mark.parametrize(
    ("hour", "text", "message"),
    [
        ("18", "   ", "Anons metni boş olamaz."),
        ("07", welcome(AYSE), "Bu saat geçti. Şu andan sonraki bir saat seçin."),
        ("24", welcome(AYSE), "Saat 00:00 ile 23:59 arasında olmalıdır."),
    ],
)
def test_change_dialog_stays_open_until_what_is_entered_is_valid(
    make_change_dialog: Callable[[int], ChangeDialog],
    app: App,
    schedule: Path,
    alerts: list[str],
    hour: str,
    text: str,
    message: str,
) -> None:
    app.choose_schedule(schedule)
    dialog = make_change_dialog(0)
    dialog.hour.set(hour)
    dialog.text.set(text)

    dialog.save()

    assert alerts == [message]
    assert dialog.window.winfo_exists()
    assert not app.changes


def test_change_dialog_tries_out_the_text_with_the_name_of_its_athlete(
    make_change_dialog: Callable[[int], ChangeDialog],
    app: App,
    schedule: Path,
    provider: FakeProvider,
) -> None:
    app.start()
    app.choose_schedule(schedule)
    wait_for(app, lambda: not app.generating)
    dialog = make_change_dialog(1)
    dialog.text.set("Merhaba {ad_soyad}.")
    calls = len(provider.calls)

    dialog.test()
    wait_for(app, lambda: app.test_status is Status.PLAYED)

    assert [text for text, _ in provider.calls[calls:]] == ["Merhaba Mehmet Demir."]
    assert not app.changes
    assert dialog.window.winfo_exists()


def test_return_in_the_change_dialog_presses_the_button_it_is_on_and_saves_elsewhere(
    make_change_dialog: Callable[[int], ChangeDialog],
    app: App,
    schedule: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.choose_schedule(schedule)
    cancelled = make_change_dialog(0)
    cancelled.minute.set("30")
    focus_on(button(cancelled.window, "Vazgeç"), monkeypatch)

    press(cancelled.window, "<Return>")

    assert not cancelled.window.winfo_exists()
    assert not app.changes
    dialog = make_change_dialog(0)
    dialog.minute.set("30")
    focus_on(dialog.reset_button, monkeypatch)
    press(dialog.window, "<Return>")
    assert dialog.window.winfo_exists()
    assert dialog.minute.get() == "00"
    assert not app.changes
    dialog.minute.set("30")
    focus_on(text_entry(dialog.window), monkeypatch)
    press(dialog.window, "<Return>")
    assert not dialog.window.winfo_exists()
    [change] = app.changes
    assert change.time == datetime.time(18, 30)


@pytest.fixture
def make_instant_dialog(
    window: MainWindow, app: App
) -> Iterator[Callable[[], InstantDialog]]:
    """Open instant announcement dialogs over the window; they are closed
    after the test."""
    dialogs: list[InstantDialog] = []

    def make() -> InstantDialog:
        dialog = InstantDialog(window.root, app)
        dialog.window.withdraw()
        dialogs.append(dialog)
        return dialog

    yield make
    for dialog in dialogs:
        if dialog.window.winfo_exists():
            dialog.window.destroy()


def test_instant_dialog_has_the_text_read_and_closes(
    make_instant_dialog: Callable[[], InstantDialog],
    window: MainWindow,
    app: App,
    clock: FakeClock,
) -> None:
    app.start()
    clock.now = at(9, 15)
    dialog = make_instant_dialog()
    dialog.text.set("  Salonu boşaltınız.  ")

    dialog.read()

    assert not dialog.window.winfo_exists()
    assert [instant.text for instant in app.instants] == ["Salonu boşaltınız."]
    window.show()
    pump(window, lambda: statuses(window) == ["Okundu"])


def test_instant_dialog_stays_open_without_a_text(
    make_instant_dialog: Callable[[], InstantDialog], app: App, alerts: list[str]
) -> None:
    dialog = make_instant_dialog()
    dialog.text.set("   ")

    dialog.read()

    assert alerts == ["Anons metni boş olamaz."]
    assert dialog.window.winfo_exists()
    assert app.instants == ()


def test_return_in_the_instant_dialog_presses_the_button_it_is_on_and_reads_elsewhere(
    make_instant_dialog: Callable[[], InstantDialog],
    app: App,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.start()
    clock.now = at(9, 15)
    cancelled = make_instant_dialog()
    cancelled.text.set("Salonu boşaltınız.")
    focus_on(button(cancelled.window, "Vazgeç"), monkeypatch)

    press(cancelled.window, "<Return>")

    assert not cancelled.window.winfo_exists()
    assert not app.instants
    dialog = make_instant_dialog()
    dialog.text.set("Salonu boşaltınız.")
    focus_on(text_entry(dialog.window), monkeypatch)
    press(dialog.window, "<Return>")
    assert not dialog.window.winfo_exists()
    assert [instant.text for instant in app.instants] == ["Salonu boşaltınız."]
