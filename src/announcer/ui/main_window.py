import datetime
import logging
import os
import subprocess
import sys
import tkinter as tk
from collections.abc import Callable, Iterable
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk
from types import TracebackType

from announcer import APP_NAME
from announcer.app import App, ChangeError, ChangeProblem
from announcer.loader import ScheduleFileError
from announcer.logo import ICON_SIZES, icon_path
from announcer.models import DELAYS, SPEEDS, WEEKDAY_NAMES
from announcer.scheduler import LATE
from announcer.template import is_valid_template
from announcer.today import BUSY, CHANGE_MARGIN, Row, Status
from announcer.ui import texts, theme
from announcer.ui.announcement_dialog import AnnouncementDialog
from announcer.ui.change_dialog import ChangeDialog
from announcer.ui.instant_dialog import InstantDialog

_log = logging.getLogger(__name__)

_POLL = 250  # milliseconds
_MINUTE = datetime.timedelta(minutes=1)
_PAD = 12
_EDGE = 16  # pixels

_PROBLEMS = frozenset(
    {
        Status.PARTLY_PLAYED,
        Status.NO_SPEECH,
        Status.ASLEEP,
        Status.NO_OUTPUT,
    }
)
# Over, and nothing to act on.
_DONE = frozenset({Status.PLAYED, Status.PASSED, Status.STOPPED})
_STRUCK = frozenset({Status.SKIPPING, Status.SKIPPED})  # not read on purpose
_EDITED_OFTEN = (Status.WAITING, Status.NEXT, Status.PLAYING, Status.PLAYED)

_WARNING_LINES = 3  # how many are shown before the rest has to be scrolled to
_TEMPLATE_TITLE = "Anons Şablonları"
_OPTIONS_TITLE = "Anons Ayarları"
_TODAY_TITLE = "Bugünün Anonsları"
_COLUMN_PADDING = 20  # pixels
# Pixels of the column that takes up the slack: below the first, a table
# leaves out the column it can do without; the column gets no narrower than
# the second.
_WANTED_TEXT_WIDTH = 160
_MIN_TEXT_WIDTH = 60
# Today's table shows the sentences that are read; the day, which the heading
# shows, gives way to them sooner.
_WANTED_SENTENCE_WIDTH = 320
_FULL_SCREEN_KEYS = ("<F11>",)
_MAC_FULL_SCREEN_KEYS = ("<F11>", "<Control-Command-f>", "<Control-Command-F>")
_NO_ROW = "none"  # the id of the line that says that a table is empty
_MAC_QUIT = "::tk::mac::Quit"  # what macOS calls for Quit in the menu and the Dock


def _moment(now: datetime.datetime) -> tuple[datetime.datetime, bool, bool, bool]:
    """Return what changes whenever the clock alone can change a row: at the
    start of a minute, when it is too late for that minute's slot, and when
    an announcement can no longer be skipped, edited or brought back."""
    minute = now.replace(second=0, microsecond=0)
    return (
        minute,
        now - minute > LATE,
        now - minute > LATE - CHANGE_MARGIN,
        now >= minute + _MINUTE - CHANGE_MARGIN,
    )


def open_file(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.run(["open", str(path)], check=False)


def _set_icon(root: tk.Tk) -> None:
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        return  # the packaged app's own icon has the margin that macOS expects
    try:
        root.iconphoto(
            True,
            *(tk.PhotoImage(master=root, file=icon_path(size)) for size in ICON_SIZES),
        )
    except tk.TclError:
        _log.exception("Cannot show the icon")  # the app is as useful without it


def tell_already_running() -> None:
    root = tk.Tk()
    root.withdraw()
    _set_icon(root)
    messagebox.showinfo(APP_NAME, texts.ALREADY_RUNNING, parent=root)
    root.destroy()


class MainWindow:
    """Everything here runs on the main thread."""

    def __init__(
        self,
        app: App,
        log_file: Path,
        clock: Callable[[], datetime.datetime] = datetime.datetime.now,
    ) -> None:
        self._app = app
        self._log_file = log_file
        self._clock = clock
        self._shown_at: tuple[datetime.datetime, bool, bool, bool] | None = None
        self._shown_rows: list[tuple[tuple[str, ...], tuple[str, ...]]] | None = None
        self._rows: tuple[Row, ...] = ()  # of today's table, as they are shown
        self._shown_announcements: list[tuple[str, tuple[str, str, str]]] | None = None
        self._shown_warnings: list[str] | None = None
        # Of today's table: the rows of the minute being read, or next.
        self._current_rows: list[str] = []
        self._reading: object = None  # the key of the row being read
        # The time and the key of the next row; the template does not change it.
        self._upcoming: tuple[datetime.time, object] | None = None
        self._update_failed = False
        self._laid_out = False
        self.closed = False

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        _set_icon(self.root)
        theme.apply(self.root)
        # The window is maximized by ``run``. What it returns to when it is not
        # leaves room around it, and its bottom is never behind the taskbar.
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width, height = min(1280, screen_width - 80), min(800, screen_height - 120)
        x = max((screen_width - width) // 2, 0)
        y = max((screen_height - height) // 2 - 40, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(1060, width), min(680, height))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)
        self.root.protocol("WM_DELETE_WINDOW", self.ask_to_close)
        self._on_mac = self.root.tk.call("tk", "windowingsystem") == "aqua"
        if self._on_mac:
            # Quit in the menu and the Dock does not go through the above.
            self.root.createcommand(_MAC_QUIT, self.ask_to_close)
        for sequence in _MAC_FULL_SCREEN_KEYS if self._on_mac else _FULL_SCREEN_KEYS:
            self.root.bind(sequence, lambda event: self.toggle_full_screen())
        self.root.bind("<Escape>", lambda event: self.leave_full_screen())
        # Tk prints an error in a button's command to a console that a
        # windowed app does not have.
        self.root.report_callback_exception = self._report_error

        # Kept on the window, as tkinter drops a font that nothing refers to.
        self._bold = font.Font(
            root=self.root, font=theme.describe(self.root, 0, "bold")
        )
        self.schedule_path = tk.StringVar()
        self.loaded = tk.StringVar()
        self.today_title = tk.StringVar()
        self.time = tk.StringVar()
        self.last_announcement = tk.StringVar()
        self.template = tk.StringVar(value=app.settings.template)
        self.end_template = tk.StringVar(value=app.settings.end_template)
        self.activity = tk.StringVar()

        # Today's announcements on the left, where the operator looks, with
        # whatever varies in height; what is rarely changed on the right.
        self._build_header().grid(row=0, sticky="ew")
        tk.Frame(self.root, background=theme.BORDER, height=1).grid(row=1, sticky="ew")
        frame = ttk.Frame(self.root, style="Window.TFrame", padding=_EDGE)
        frame.grid(row=2, sticky="nsew")
        frame.columnconfigure(0, weight=5, uniform="column")
        frame.columnconfigure(1, weight=3, uniform="column")
        frame.rowconfigure(0, weight=1)

        left = ttk.Frame(frame, style="Window.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, _PAD // 2))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        self.warning_box = self._build_warnings(left)
        self.warning_box.grid(row=0, sticky="ew", pady=(0, _PAD))
        self._build_today(left).grid(row=1, sticky="nsew")
        self._build_schedule(left).grid(row=2, sticky="ew", pady=(_PAD, 0))

        right = ttk.Frame(frame, style="Window.TFrame")
        right.grid(row=0, column=1, sticky="nsew", padx=(_PAD // 2, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        self._build_template(right).grid(row=0, sticky="ew")
        self._build_announcements(right).grid(row=1, sticky="nsew", pady=(_PAD, 0))
        self._build_options(right).grid(row=2, sticky="ew", pady=(_PAD, 0))

        self._build_footer(frame).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(_PAD, 0)
        )

        self.show()
        self._after = self.root.after(_POLL, self._tick)

    def run(self) -> None:
        self.maximize()
        self.root.mainloop()

    def maximize(self) -> None:
        try:
            self.root.state("zoomed")
        except tk.TclError:
            _log.exception("Cannot maximize the window")  # it is usable as it is

    def toggle_full_screen(self) -> None:
        on = not self.root.attributes("-fullscreen")
        self.root.attributes("-fullscreen", on)
        _log.info("Full screen %s", "on" if on else "off")

    def leave_full_screen(self) -> None:
        if self.root.attributes("-fullscreen"):
            self.toggle_full_screen()

    def ask_to_close(self) -> None:
        """Closing the window ends the announcements."""
        if messagebox.askyesno(
            APP_NAME, texts.CLOSE_QUESTION, default="no", parent=self.root
        ):
            _log.info("Closed by the operator")
            self.close()

    def close(self) -> None:
        self.closed = True
        self.root.after_cancel(self._after)
        if self._on_mac:
            self.root.deletecommand(_MAC_QUIT)
        # The handler ties the window and tkinter together in a circle; without
        # it tkinter is let go of right here, on the thread it belongs to.
        del self.root.report_callback_exception
        self.root.destroy()

    def show(self) -> None:
        if self.closed:
            return  # closed while a dialog was open
        app, now = self._app, self._clock()
        self._shown_at = _moment(now)
        path = app.settings.schedule_path
        self.schedule_path.set("" if path is None else str(path))
        self.choose_button.configure(
            style="Primary.TButton" if path is None else "TButton"
        )
        self._schedule_entry.xview_moveto(1)
        self.loaded.set(texts.last_loaded(app.refreshed_at))
        self.today_title.set(texts.today_title(now))
        self.time.set(f"{now:%H:%M:%S}")
        self.last_announcement.set(texts.last_announcement(app.last_announcement))
        self.activity.set(texts.activity(app.generating, app.test_status))
        self.speed_box.current(SPEEDS.index(app.settings.speed))
        self.start_delay_box.current(DELAYS.index(app.settings.start_delay))
        self.end_delay_box.current(DELAYS.index(app.settings.end_delay))
        self.end_box.current(0 if app.settings.end_announcements else 1)
        self._show_today(now)
        self._show_announcements()
        self._show_warnings()
        self._show_template_button()

    def _build_header(self) -> ttk.Frame:
        header = ttk.Frame(self.root, padding=(_EDGE, 10))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, textvariable=self.today_title, style="Heading.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, textvariable=self.time, style="Heading.TLabel").grid(
            row=0, column=1, sticky="e"
        )
        return header

    @staticmethod
    def _card(
        parent: ttk.Frame, title: str, note: tk.StringVar | None = None
    ) -> tuple[tk.Frame, ttk.Frame]:
        card = theme.card(parent)
        card.columnconfigure(1 if note else 0, weight=1)
        card.rowconfigure(1, weight=1)
        ttk.Label(card, text=title, style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        if note:
            ttk.Label(card, textvariable=note, style="Note.TLabel").grid(
                row=0, column=1, sticky="e", padx=(16, 0), pady=(0, 8)
            )
        body = ttk.Frame(card)
        body.grid(row=1, column=0, columnspan=2, sticky="nsew")
        return card, body

    def _build_schedule(self, parent: ttk.Frame) -> tk.Frame:
        # There is no button to read the file again: saving it does that.
        card, body = self._card(parent, "Sporcu Listesi", self.loaded)
        body.columnconfigure(0, weight=1)
        self._schedule_entry = ttk.Entry(
            body,
            textvariable=self.schedule_path,
            state="readonly",
            style="Shown.TEntry",
        )
        self._schedule_entry.grid(row=0, column=0, sticky="ew")
        # The file's name is at the end of the path, so that end is kept in
        # view, also once the window is laid out and whenever it is resized.
        self._schedule_entry.bind(
            "<Configure>", lambda event: self._schedule_entry.xview_moveto(1)
        )
        self.choose_button = ttk.Button(body, text="Dosya Seç…", command=self.choose)
        self.choose_button.grid(row=0, column=1, padx=(8, 0))
        return card

    def _build_today(self, parent: ttk.Frame) -> tk.Frame:
        card, body = self._card(parent, _TODAY_TITLE, self.last_announcement)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        actions = ttk.Frame(body)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        actions.columnconfigure(3, weight=1)
        self.replay_button = ttk.Button(actions, text="Tekrar Oku", command=self.replay)
        self.edit_row_button = ttk.Button(
            actions, text="Düzenle…", command=self.edit_row
        )
        self.skip_button = ttk.Button(actions, text=texts.SKIP, command=self.skip)
        self.stop_button = ttk.Button(
            actions, text="Durdur", style="Danger.TButton", command=self.stop
        )
        self.instant_button = ttk.Button(
            actions, text="Anlık Anons…", style="Primary.TButton", command=self.announce
        )
        # The first three act on the selected row; the last two do not, so
        # they stand apart.
        for column, button in enumerate(
            (self.replay_button, self.edit_row_button, self.skip_button)
        ):
            button.grid(row=0, column=column, padx=(0, 8))
        self.stop_button.grid(row=0, column=4, padx=(8, 0))
        self.instant_button.grid(row=0, column=5, padx=(8, 0))

        # A rare status of an edited row may be cut short at its end.
        statuses = [
            *(texts.STATUS[status] for status in Status),
            *(f"{texts.STATUS[status]} {texts.EDITED}" for status in _EDITED_OFTEN),
        ]
        self.today = self._table(
            body,
            row=1,
            height=8,
            columns={
                "day": "Gün",
                "time": "Saat",
                "kind": "Tür",
                "text": "Anons",
                "status": "Durum",
            },
            widths={
                "day": self._widest(WEEKDAY_NAMES),
                "time": self._widest(["00:00"]),
                "kind": self._widest(texts.KINDS.values()),
                "text": 0,
                "status": self._widest(statuses),
            },
            optional="day",
            wanted=_WANTED_SENTENCE_WIDTH,
        )
        self.today.bind("<Configure>", self._on_table_laid_out, add=True)
        self.today.bind("<<TreeviewSelect>>", lambda event: self._show_row_buttons())
        # Opening the editor is harmless; reading out loud is not, so neither a
        # double click nor a key reads a row.
        self.today.bind("<Double-1>", self._on_double_click)
        self.today.bind("<Return>", lambda event: self.edit_row())
        struck = theme.describe(self.root, 0, "overstrike")
        self.today.tag_configure(Status.NEXT.name, font=self._bold)
        self.today.tag_configure(
            Status.PLAYING.name, font=self._bold, foreground=theme.BLUE
        )
        for status in (Status.PREPARING, Status.QUEUED):
            self.today.tag_configure(status.name, foreground=theme.BLUE)
        for status in _DONE:
            self.today.tag_configure(status.name, foreground=theme.MUTED)
        for status in _PROBLEMS:
            self.today.tag_configure(status.name, foreground=theme.RED)
        for status in _STRUCK:
            self.today.tag_configure(status.name, foreground=theme.MUTED, font=struck)
        self.today.tag_configure(_NO_ROW, foreground=theme.MUTED)
        return card

    def _build_options(self, parent: ttk.Frame) -> tk.Frame:
        card, body = self._card(parent, _OPTIONS_TITLE)
        body.columnconfigure(1, weight=1)
        self.speed_box = self._choice(
            body, 0, "Ses hızı", map(texts.speed, SPEEDS), self.choose_speed
        )
        self.start_delay_box = self._choice(
            body,
            1,
            "Başlangıç gecikme süresi",
            map(texts.delay, DELAYS),
            self.choose_start_delay,
        )
        self.end_delay_box = self._choice(
            body,
            2,
            "Bitiş gecikme süresi",
            map(texts.delay, DELAYS),
            self.choose_end_delay,
        )
        self.end_box = self._choice(
            body,
            3,
            "Bitiş anonsu",
            map(texts.switch, (True, False)),
            self.choose_end_announcements,
        )
        return card

    @staticmethod
    def _choice(
        body: ttk.Frame,
        row: int,
        name: str,
        values: Iterable[str],
        command: Callable[[], None],
    ) -> ttk.Combobox:
        gap = 8 if row else 0
        ttk.Label(body, text=name).grid(row=row, column=0, sticky="w", pady=(gap, 0))
        choice = ttk.Combobox(
            body,
            values=tuple(values),
            state="readonly",
            width=15,
            cursor=theme.POINTER,
        )
        choice.grid(row=row, column=1, sticky="e", padx=(12, 0), pady=(gap, 0))

        def chosen(event: object) -> None:
            choice.selection_clear()  # Windows marks the text that was chosen
            command()

        choice.bind("<<ComboboxSelected>>", chosen)
        # The wheel is for scrolling the tables: over a choice it would change
        # a setting by accident. A trackpad has an event of its own.
        for sequence in choice.bind_class("TCombobox"):
            if "Wheel" in sequence or "Scroll" in sequence:
                choice.bind(sequence, lambda event: "break")
        return choice

    def _build_template(self, parent: ttk.Frame) -> tk.Frame:
        card, body = self._card(parent, _TEMPLATE_TITLE)
        body.columnconfigure(1, weight=1)
        self.test_buttons: list[ttk.Button] = []
        for row, (name, variable) in enumerate(
            (("Başlangıç", self.template), ("Bitiş", self.end_template))
        ):
            gap = (8 if row else 0, 0)
            ttk.Label(body, text=name).grid(row=row, column=0, sticky="w", pady=gap)
            entry = ttk.Entry(body, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=gap)
            entry.bind("<Return>", lambda event: self.save_template())
            variable.trace_add("write", lambda *args: self._show_template_button())
            button = ttk.Button(body, text="Test", command=self._tester(variable))
            button.grid(row=row, column=2, sticky="ew", padx=(8, 0), pady=gap)
            self.test_buttons.append(button)
        self.test_button, self.end_test_button = self.test_buttons
        note = ttk.Label(
            body, text=f"{texts.TEMPLATE_HINT} {texts.END_HINT}", style="Note.TLabel"
        )
        note.grid(row=2, column=0, columnspan=2, sticky="new", pady=(8, 0))
        note.bind(
            "<Configure>", lambda event: note.configure(wraplength=max(event.width, 1))
        )
        self.save_template_button = ttk.Button(
            body, text="Kaydet", style="Primary.TButton", command=self.save_template
        )
        self.save_template_button.grid(
            row=2, column=2, sticky="new", padx=(8, 0), pady=(8, 0)
        )
        return card

    def _tester(self, template: tk.StringVar) -> Callable[[], None]:
        return lambda: self.test(template.get())

    def _build_announcements(self, parent: ttk.Frame) -> tk.Frame:
        card, body = self._card(parent, "Özel Anonslar")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        buttons = ttk.Frame(card)
        buttons.grid(row=0, column=1, sticky="e", pady=(0, 8))
        self.add_button = ttk.Button(buttons, text="Ekle…", command=self.add)
        self.edit_button = ttk.Button(buttons, text="Düzenle…", command=self.edit)
        self.delete_button = ttk.Button(buttons, text="Sil", command=self.delete)
        for column, button in enumerate(
            (self.add_button, self.edit_button, self.delete_button)
        ):
            button.grid(row=0, column=column, padx=(6, 0))
        self.announcements = self._table(
            body,
            row=0,
            height=3,
            columns={"time": "Saat", "days": "Günler", "text": "Anons"},
            widths={
                "time": self._widest(["00:00"]),
                "days": self._widest([texts.days((1, 3))]),
                "text": 0,
            },
            optional="days",
        )
        self.announcements.tag_configure(_NO_ROW, foreground=theme.MUTED)
        self.announcements.bind(
            "<<TreeviewSelect>>", lambda event: self._show_buttons()
        )
        self.announcements.bind("<Double-1>", lambda event: self.edit())
        self.announcements.bind("<Return>", lambda event: self.edit())
        self.announcements.bind("<Delete>", lambda event: self.delete())
        self.announcements.bind("<BackSpace>", lambda event: self.delete())
        return card

    def _build_warnings(self, parent: ttk.Frame) -> tk.Frame:
        box = tk.Frame(
            parent,
            background=theme.WARNING_BACKGROUND,
            highlightbackground=theme.WARNING_BORDER,
            highlightthickness=1,
            padx=14,
            pady=10,
        )
        box.columnconfigure(0, weight=1)
        tk.Label(
            box,
            text="Uyarılar",
            font=self._bold,
            background=theme.WARNING_BACKGROUND,
            foreground=theme.WARNING_TEXT,
        ).grid(row=0, column=0, sticky="w")
        self.warnings = tk.Text(
            box,
            height=1,
            wrap="word",
            state="disabled",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font="TkDefaultFont",
            cursor="arrow",
            background=theme.WARNING_BACKGROUND,
            foreground=theme.WARNING_TEXT,
        )
        self.warnings.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        # A warning that is wider than the window takes more than one line.
        self.warnings.bind("<Configure>", lambda event: self._fit_warnings())
        self._warnings_scrollbar = ttk.Scrollbar(
            box, orient="vertical", command=self.warnings.yview
        )
        self._warnings_scrollbar.grid(row=1, column=1, sticky="ns")
        self.warnings.configure(yscrollcommand=self._warnings_scrollbar.set)
        return box

    def _build_footer(self, parent: ttk.Frame) -> ttk.Frame:
        footer = ttk.Frame(parent, style="Window.TFrame")
        footer.columnconfigure(1, weight=1)
        ttk.Label(footer, text=texts.SLEEP_NOTICE, style="Window.Note.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(footer, textvariable=self.activity, style="Window.TLabel").grid(
            row=0, column=1, sticky="e", padx=_PAD
        )
        self.clear_button = ttk.Button(footer, text=texts.CLEAR, command=self.clear)
        self.clear_button.grid(row=0, column=2, padx=(0, 8))
        self.log_button = ttk.Button(footer, text="Kayıtlar", command=self.open_log)
        self.log_button.grid(row=0, column=3)
        return footer

    def _widest(self, values: Iterable[str]) -> int:
        """Wide enough for ``values`` also when they are in bold."""
        return max(self._bold.measure(value) for value in values) + _COLUMN_PADDING

    @staticmethod
    def _table(
        parent: ttk.Frame,
        row: int,
        height: int,
        columns: dict[str, str],
        widths: dict[str, int],
        optional: str | None = None,
        wanted: int = _WANTED_TEXT_WIDTH,
    ) -> ttk.Treeview:
        """The "text" column takes up the width that is left over, and gives
        way in a narrow window; before it does, the ``optional`` column is
        left out.
        """
        frame = tk.Frame(parent, highlightbackground=theme.BORDER, highlightthickness=1)
        frame.grid(row=row, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        table = ttk.Treeview(
            frame,
            columns=tuple(columns),
            show="headings",
            height=height,
            selectmode="browse",
        )
        for name, heading in columns.items():
            table.heading(name, text=heading, anchor="w")
            minwidth = _MIN_TEXT_WIDTH if name == "text" else widths[name]
            table.column(name, width=widths[name], minwidth=minwidth, stretch=False)

        def fit(event: tk.Event[ttk.Treeview]) -> None:
            if event.width <= 1:
                return  # not laid out yet
            fixed = sum(width for name, width in widths.items() if name != "text")
            shown = tuple(columns)
            if optional and event.width - fixed < wanted:
                shown = tuple(name for name in columns if name != optional)
                fixed -= widths[optional]
            if tuple(table.cget("displaycolumns")) != shown:
                table.configure(displaycolumns=shown)
            table.column("text", width=max(event.width - fixed, _MIN_TEXT_WIDTH))

        table.bind("<Configure>", fit, add=True)
        table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")

        def scrolled(first: float, last: float) -> None:
            scrollbar.set(first, last)
            if float(first) <= 0 and float(last) >= 1:
                scrollbar.grid_remove()
            else:
                scrollbar.grid()

        table.configure(yscrollcommand=scrolled)
        return table

    def _show_today(self, now: datetime.datetime) -> None:
        rows = self._app.rows()
        day = WEEKDAY_NAMES[now.weekday()]
        shown: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
            (
                (
                    day,
                    f"{row.time:%H:%M}",
                    texts.KINDS[row.kind],
                    row.text,
                    texts.status(row),
                ),
                (row.status.name,),
            )
            for row in rows
        ]
        if shown == self._shown_rows:
            # Left alone, so that it keeps its place while scrolled. The rows
            # are the same ones in the same places, but whether they can be
            # changed, and what is sounding, may not be.
            self._rows = rows
            self._show_row_buttons()
            return
        self._shown_rows = shown
        selected = self._selected_row()
        scrolled_to = self.today.yview()[0]
        self._rows = rows
        self.today.delete(*self.today.get_children())
        for index, (values, tags) in enumerate(shown):
            self.today.insert("", "end", iid=str(index), values=values, tags=tags)
        if not rows:
            self.today.insert(
                "",
                "end",
                iid=_NO_ROW,
                values=("", "", "", texts.NO_ROWS, ""),
                tags=_NO_ROW,
            )
        if selected is not None:
            same = [
                str(index) for index, row in enumerate(rows) if _same(row, selected)
            ]
            self.today.selection_set(same[:1])
        self._show_row_buttons()
        current = [
            index
            for index, row in enumerate(rows)
            if row.status in BUSY or row.status is Status.NEXT
        ]
        self._current_rows = [
            str(index) for index in current if rows[index].time == rows[current[0]].time
        ]
        upcoming = next(
            ((row.time, row.key) for row in rows if row.status is Status.NEXT),
            None,
        )
        reading = next(
            (
                str(index)
                for index, row in enumerate(rows)
                if row.status is Status.PLAYING
            ),
            None,
        )
        if upcoming != self._upcoming:
            self._upcoming = upcoming
            self._see_current()
        else:
            # The day has not moved on, so the table stays where the operator is.
            self.today.yview_moveto(scrolled_to)
        if reading is not None and rows[int(reading)].key != self._reading:
            self.today.see(reading)
        self._reading = None if reading is None else rows[int(reading)].key

    def _on_table_laid_out(self, event: tk.Event[ttk.Treeview]) -> None:
        """Only once the table has its size: a later resize must not undo what
        the operator scrolled to."""
        if event.height > 1 and not self._laid_out:
            self._laid_out = True
            self._see_current()

    def _see_current(self) -> None:
        """Bring the whole minute in view, its first row last so that it is the
        one that shows if not all of them fit."""
        if self._current_rows:
            self.today.see(self._current_rows[-1])
            self.today.see(self._current_rows[0])

    def _selected_row(self) -> Row | None:
        """A row's id in today's table is its index among the rows shown."""
        selected = self.today.selection()
        if not selected or selected[0] == _NO_ROW:
            return None
        return self._rows[int(selected[0])]

    def _show_row_buttons(self) -> None:
        row = self._selected_row()
        replayable = row is not None and row.replayable
        editable = row is not None and row.editable
        skippable = row is not None and row.skippable
        skipped = row is not None and row.status is Status.SKIPPING
        self.replay_button.state(["!disabled" if replayable else "disabled"])
        self.edit_row_button.state(["!disabled" if editable else "disabled"])
        self.skip_button.state(["!disabled" if skippable else "disabled"])
        self.skip_button.configure(text=texts.UNSKIP if skipped else texts.SKIP)
        self.stop_button.state(["!disabled" if self._app.sounding else "disabled"])

    def _on_double_click(self, event: tk.Event[ttk.Treeview]) -> None:
        if self.today.identify_row(event.y):  # not on the headings or below the rows
            self.edit_row()

    def _show_announcements(self) -> None:
        """A row's id is the index of its custom announcement."""
        announcements = self._app.settings.custom_announcements
        shown = [
            (
                str(index),
                (f"{item.time:%H:%M}", texts.days(item.weekdays), item.text),
            )
            for index, item in sorted(
                enumerate(announcements), key=lambda pair: pair[1].time
            )
        ]
        if shown == self._shown_announcements:
            return  # left alone, so that it keeps its place while scrolled
        self._shown_announcements = shown
        selected = self.announcements.selection()
        self.announcements.delete(*self.announcements.get_children())
        for row, values in shown:
            self.announcements.insert("", "end", iid=row, values=values)
        if not shown:
            self.announcements.insert(
                "",
                "end",
                iid=_NO_ROW,
                values=("", "", texts.NO_ANNOUNCEMENTS),
                tags=_NO_ROW,
            )
        if selected and self.announcements.exists(selected[0]):
            self.announcements.selection_set(selected[0])
        self._show_buttons()

    def _selected(self) -> int | None:
        selected = self.announcements.selection()
        return int(selected[0]) if selected and selected[0] != _NO_ROW else None

    def _show_buttons(self) -> None:
        state = "disabled" if self._selected() is None else "!disabled"
        self.edit_button.state([state])
        self.delete_button.state([state])

    def _show_template_button(self) -> None:
        settings = self._app.settings
        changed = (
            self.template.get().strip(),
            self.end_template.get().strip(),
        ) != (settings.template, settings.end_template)
        self.save_template_button.state(["!disabled" if changed else "disabled"])

    def _show_warnings(self) -> None:
        lines = texts.warnings(self._app)
        if lines == self._shown_warnings:
            return
        self._shown_warnings = lines
        self.warnings.configure(state="normal")
        self.warnings.delete("1.0", "end")
        self.warnings.insert("1.0", "\n".join(f"• {line}" for line in lines))
        self.warnings.configure(state="disabled")
        if lines:
            self.warning_box.grid()
            self._fit_warnings()
        else:
            self.warning_box.grid_remove()

    def _fit_warnings(self) -> None:
        self.warnings.update_idletasks()  # so that the lines are laid out
        if self.warnings.winfo_width() > 1:
            lines = self.warnings.count("1.0", "end", "displaylines", return_ints=True)
        else:
            # The window is not laid out yet, so nothing wraps so far. The
            # box is fitted again as soon as it gets its width.
            lines = int(self.warnings.index("end-1c").split(".")[0])
        height = max(1, min(lines, _WARNING_LINES))
        if height != int(self.warnings.cget("height")):
            self.warnings.configure(height=height)
        if lines > _WARNING_LINES:
            self._warnings_scrollbar.grid()
        else:
            self._warnings_scrollbar.grid_remove()

    def _tick(self) -> None:
        try:
            changed = self._app.poll()
            now = self._clock()
            self.time.set(f"{now:%H:%M:%S}")
            if changed or _moment(now) != self._shown_at:
                self.show()
            self._update_failed = False
        except Exception:
            # Not shown in a box, and logged once: an error that stays would
            # come up again four times a second.
            if not self._update_failed:
                _log.exception("Bringing the window up to date failed")
            self._update_failed = True
        finally:
            self._after = self.root.after(_POLL, self._tick)

    def choose(self) -> None:
        current = self._app.settings.schedule_path
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="Sporcu Listesini Seç",
            filetypes=[("Excel dosyası", "*.xlsx")],
            initialdir=None if current is None else current.parent,
        )
        if not chosen:
            return
        try:
            self._app.choose_schedule(Path(chosen))
        except ScheduleFileError as error:
            _log.warning("Cannot use %s: %s (%r)", chosen, error, error.__cause__)
            self._alert("Sporcu Listesi", texts.schedule_error(error))
        except OSError:
            _log.exception("Cannot save the choice of schedule file")
            self._alert("Sporcu Listesi", texts.CANNOT_SAVE)
        self.show()

    def save_template(self) -> None:
        template = self.template.get().strip()
        end_template = self.end_template.get().strip()
        settings = self._app.settings
        if (template, end_template) == (settings.template, settings.end_template):
            return
        for problem, wanted in (
            (texts.START_NEEDS_PLACEHOLDER, template),
            (texts.END_NEEDS_PLACEHOLDER, end_template),
        ):
            if not is_valid_template(wanted):
                self._alert(_TEMPLATE_TITLE, problem)
                return
        try:
            self._app.set_templates(template, end_template)
        except ValueError:
            self._alert(_TEMPLATE_TITLE, texts.TEMPLATE_NEEDS_PLACEHOLDER)
        except OSError:
            _log.exception("Cannot save the templates")
            self._alert(_TEMPLATE_TITLE, texts.CANNOT_SAVE)
        self.show()

    def test(self, template: str) -> None:
        try:
            self._app.test(template.strip())
        except ValueError:
            self._alert(_TEMPLATE_TITLE, texts.TEMPLATE_NEEDS_PLACEHOLDER)
        self.show()

    def replay(self) -> None:
        row = self._selected_row()
        if row is not None and self._app.replay(row):
            self.show()

    def edit_row(self) -> None:
        row = self._selected_row()
        if row is None or not row.editable:
            return
        editing = self._app.editing(row)
        if editing is None:
            self._alert(_TODAY_TITLE, texts.CHANGE_PROBLEMS[ChangeProblem.GONE])
            self.show()
            return
        ChangeDialog(self.root, self._app, row, editing).wait()
        self.show()

    def skip(self) -> None:
        row = self._selected_row()
        if row is None or not row.skippable:
            return
        try:
            self._app.skip(row, row.status is not Status.SKIPPING)
        except ChangeError as error:
            self._alert(_TODAY_TITLE, texts.CHANGE_PROBLEMS[error.problem])
        except OSError:
            _log.exception("Cannot save the skipping of an announcement")
            self._alert(_TODAY_TITLE, texts.CANNOT_SAVE)
        self.show()

    def announce(self) -> None:
        InstantDialog(self.root, self._app).wait()
        self.show()

    def stop(self) -> None:
        self._app.cut()
        self.show()

    def choose_speed(self) -> None:
        speed = SPEEDS[self.speed_box.current()]
        if speed != self._app.settings.speed:
            self._change(self._app.set_speed, speed)

    def choose_start_delay(self) -> None:
        delay = DELAYS[self.start_delay_box.current()]
        if delay != self._app.settings.start_delay:
            self._change(self._app.set_start_delay, delay)

    def choose_end_delay(self) -> None:
        delay = DELAYS[self.end_delay_box.current()]
        if delay != self._app.settings.end_delay:
            self._change(self._app.set_end_delay, delay)

    def choose_end_announcements(self) -> None:
        on = self.end_box.current() == 0
        if on != self._app.settings.end_announcements:
            self._change(self._app.set_end_announcements, on)

    def _change[Value](self, setting: Callable[[Value], None], value: Value) -> None:
        try:
            setting(value)
        except OSError:
            _log.exception("Cannot save a setting")
            self._alert(_OPTIONS_TITLE, texts.CANNOT_SAVE)
        self.show()  # with what is in use, so the old value after a failure

    def add(self) -> None:
        AnnouncementDialog(self.root, self._app).wait()
        self.show()

    def edit(self) -> None:
        selected = self._selected()
        if selected is not None:
            AnnouncementDialog(self.root, self._app, selected).wait()
            self.show()

    def delete(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        announcement = self._app.settings.custom_announcements[selected]
        if not messagebox.askyesno(
            "Özel Anonsu Sil",
            f"“{announcement.text}” anonsu silinsin mi?",
            parent=self.root,
        ):
            return
        try:
            self._app.delete_announcement(selected)
        except OSError:
            _log.exception("Cannot save the deletion of a custom announcement")
            self._alert("Özel Anonslar", texts.CANNOT_SAVE)
        self.announcements.selection_set()
        self.show()

    def clear(self) -> None:
        if not messagebox.askyesno(
            texts.CLEAR, texts.CLEAR_QUESTION, default="no", parent=self.root
        ):
            return
        try:
            self._app.clear()
        except OSError:
            _log.exception("Cannot clear the data")
            self._alert(texts.CLEAR, texts.CANNOT_CLEAR)
        else:
            self.template.set(self._app.settings.template)
            self.end_template.set(self._app.settings.end_template)
            self.today.selection_set()
            self.announcements.selection_set()
        self.show()

    def open_log(self) -> None:
        if self._log_file.is_file():
            open_file(self._log_file)
        else:
            self._alert("Kayıtlar", "Kayıt dosyası bulunamadı.")

    def _alert(self, title: str, message: str) -> None:
        if not self.closed:
            messagebox.showerror(title, message, parent=self.root)

    def _report_error(
        self,
        kind: type[BaseException],
        error: BaseException,
        trace: TracebackType | None,
    ) -> None:
        _log.error("An action in the window failed", exc_info=(kind, error, trace))
        self._alert(
            APP_NAME, "Beklenmeyen bir hata oluştu. Ayrıntılar kayıt dosyasındadır."
        )


def _same(row: Row, other: Row) -> bool:
    """Whether two rows are of the same announcement, whatever became of it."""
    return row.key == other.key
