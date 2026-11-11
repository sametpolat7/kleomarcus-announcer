import datetime
import logging
import os
import subprocess
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk
from types import TracebackType

from announcer import APP_NAME
from announcer.app import App
from announcer.loader import ScheduleFileError
from announcer.logo import ICON_SIZES, icon_path
from announcer.models import WEEKDAY_NAMES
from announcer.scheduler import LATE
from announcer.today import Status
from announcer.ui import texts
from announcer.ui.announcement_dialog import AnnouncementDialog

_log = logging.getLogger(__name__)

_POLL = 250  # milliseconds between two looks at what the app has to report
_PAD = 12

_PROBLEMS = frozenset(
    {
        Status.PARTLY_PLAYED,
        Status.NO_SPEECH,
        Status.ASLEEP,
        Status.NO_OUTPUT,
        Status.CONFLICT,
    }
)
_DONE = frozenset({Status.PLAYED, Status.PASSED})  # over, and nothing to act on

# Colours that read well on a light and on a dark window alike.
_GREY = "#808080"
_RED = "#c0392b"
_BLUE = "#2e86de"
# The warnings stand on a colour of their own, so their text has one too.
_WARNING_BACKGROUND = "#fff3cd"
_WARNING_TEXT = "#4d3b00"
_WARNING_LINES = 3  # how many are shown before the rest has to be scrolled to
_TEMPLATE_TITLE = "Varsayılan Anons Şablonu"
_NO_ROW = "none"  # the id of the line that says that a table is empty
_MAC_QUIT = "::tk::mac::Quit"  # what macOS calls for Quit in the menu and the Dock


def _moment(now: datetime.datetime) -> tuple[datetime.datetime, bool]:
    """Return what changes whenever the clock alone can change a status: at
    the start of a minute, and when it is too late for that minute's slot."""
    minute = now.replace(second=0, microsecond=0)
    return minute, now - minute > LATE


def _resized(base: font.Font, by: int) -> font.Font:
    copy = base.copy()
    size = int(copy.cget("size"))  # negative when it is in pixels
    copy.configure(size=size + by if size > 0 else size - by)
    return copy


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
    root.withdraw()  # only the message is shown
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
        self._shown_at: tuple[datetime.datetime, bool] | None = None
        self._shown_rows: list[tuple[str, str, str, str]] | None = None
        self._shown_announcements: list[tuple[str, tuple[str, str, str]]] | None = None
        self._shown_warnings: list[str] | None = None
        self._current_row: str | None = None  # of today's table: sounding, or next
        self._update_failed = False  # the last look at the app raised an error
        self.closed = False

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        _set_icon(self.root)
        # Small enough for a laptop screen; the tables take up the difference.
        screen_height = self.root.winfo_screenheight()
        height = min(800, screen_height - 120)
        # A little above the middle of the screen. Left to itself, Windows may
        # put the window so low that its bottom is behind the taskbar.
        left = max((self.root.winfo_screenwidth() - 860) // 2, 0)
        top = max((screen_height - height) // 2 - 40, 0)
        self.root.geometry(f"860x{height}+{left}+{top}")
        # At 760 high everything still fits, with three lines of warnings. On
        # a lower screen the window stays in view and today's table gives way.
        self.root.minsize(720, min(760, height))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.protocol("WM_DELETE_WINDOW", self.ask_to_close)
        self._on_mac = self.root.tk.call("tk", "windowingsystem") == "aqua"
        if self._on_mac:
            # Quit in the menu and the Dock does not go through the above.
            self.root.createcommand(_MAC_QUIT, self.ask_to_close)
        # Tk prints an error in a button's command to a console that a
        # windowed app does not have.
        self.root.report_callback_exception = self._report_error

        self.schedule_path = tk.StringVar()
        self.loaded = tk.StringVar()
        self.today_title = tk.StringVar()
        self.time = tk.StringVar()
        self.last_announcement = tk.StringVar()
        self.template = tk.StringVar(value=app.settings.template)
        self.activity = tk.StringVar()

        # Kept on the window, as tkinter drops a font that nothing refers to.
        self._bold = font.nametofont("TkDefaultFont").copy()
        self._bold.configure(weight="bold")
        self._large = _resized(self._bold, 4)  # the date and the time
        self._title = _resized(self._bold, 1)  # the name of a section
        self._small = _resized(font.nametofont("TkDefaultFont"), -2)  # a note

        # Top to bottom: what the operator looks at, then what is rarely
        # changed. Today's table takes whatever height is left over.
        frame = ttk.Frame(self.root, padding=_PAD)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1, minsize=110)
        self._build_header(frame).grid(row=0, sticky="ew")
        self.warning_box = self._build_warnings(frame)
        self.warning_box.grid(row=1, sticky="ew", pady=(_PAD, 0))
        self._build_today(frame).grid(row=2, sticky="nsew", pady=(_PAD, 0))
        self._build_template(frame).grid(row=3, sticky="ew", pady=(_PAD, 0))
        self._build_announcements(frame).grid(row=4, sticky="ew", pady=(_PAD, 0))
        self._build_schedule(frame).grid(row=5, sticky="ew", pady=(_PAD, 0))
        self._build_footer(frame).grid(row=6, sticky="ew", pady=(_PAD, 0))

        self.show()
        self._after = self.root.after(_POLL, self._tick)

    def run(self) -> None:
        self.root.mainloop()

    def ask_to_close(self) -> None:
        """Close the window if the operator means it: it ends the announcements."""
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
            return  # closed while a dialog was open; there is nothing to show
        app, now = self._app, self._clock()
        self._shown_at = _moment(now)
        path = app.settings.schedule_path
        self.schedule_path.set("" if path is None else str(path))
        self._schedule_entry.xview_moveto(1)
        self.loaded.set(texts.last_loaded(app.refreshed_at))
        self.today_title.set(texts.today_title(now))
        self.time.set(f"{now:%H:%M:%S}")
        self.last_announcement.set(texts.last_announcement(app.last_announcement))
        self.activity.set(texts.activity(app.generating, app.test_status))
        self._show_today(now)
        self._show_announcements()
        self._show_warnings()
        self._show_template_button()

    def _build_header(self, parent: ttk.Frame) -> ttk.Frame:
        header = ttk.Frame(parent)
        header.columnconfigure(0, weight=1)
        ttk.Label(header, textvariable=self.today_title, font=self._large).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, textvariable=self.time, font=self._large).grid(
            row=0, column=1, sticky="e"
        )
        return header

    def _section(
        self, parent: ttk.Frame, title: str
    ) -> tuple[ttk.Frame, ttk.LabelFrame]:
        """Return one part of the window, and the box in it under its name.

        The part is what is placed in the window; the box is what is filled.
        """
        section = ttk.Frame(parent)
        section.columnconfigure(0, weight=1)
        section.rowconfigure(1, weight=1)
        ttk.Label(section, text=title, font=self._title).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        box = ttk.LabelFrame(section, padding=8)
        # A box keeps room for a name of its own; one of no size takes none.
        box.configure(labelwidget=ttk.Frame(box))
        box.grid(row=1, column=0, sticky="nsew")
        return section, box

    def _note(self, parent: tk.Misc, text: str | tk.StringVar) -> ttk.Label:
        note = ttk.Label(parent, font=self._small, foreground=_GREY)
        if isinstance(text, str):
            note.configure(text=text)
        else:
            note.configure(textvariable=text)
        return note

    def _build_schedule(self, parent: ttk.Frame) -> ttk.Frame:
        section, box = self._section(parent, "Sporcu Listesi")
        box.columnconfigure(0, weight=1)
        self._schedule_entry = ttk.Entry(
            box, textvariable=self.schedule_path, state="readonly"
        )
        self._schedule_entry.grid(row=0, column=0, sticky="ew")
        # The file's name is at the end of the path, so that end is kept in
        # view, also once the window is laid out and whenever it is resized.
        self._schedule_entry.bind(
            "<Configure>", lambda event: self._schedule_entry.xview_moveto(1)
        )
        self.choose_button = ttk.Button(box, text="Dosya Seç…", command=self.choose)
        self.choose_button.grid(row=0, column=1, padx=(8, 0))
        # There is no button to read the file again: saving it does that.
        self._note(box, self.loaded).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        return section

    def _build_today(self, parent: ttk.Frame) -> ttk.Frame:
        section, box = self._section(parent, "Bugünün Anonsları")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.today = self._table(
            box,
            height=6,
            columns={
                "day": "Gün",
                "time": "Saat",
                "text": "Ad Soyad",
                "status": "Durum",
            },
            widths={"day": 90, "time": 60, "text": 360, "status": 150},
            select=False,  # there is nothing to do with a row but read it
        )
        self.today.bind("<Configure>", self._on_table_laid_out)
        self.today.tag_configure(Status.NEXT.name, font=self._bold)
        self.today.tag_configure(Status.PLAYING.name, font=self._bold, foreground=_BLUE)
        for status in _DONE:
            self.today.tag_configure(status.name, foreground=_GREY)
        for status in _PROBLEMS:
            self.today.tag_configure(status.name, foreground=_RED)
        self.today.tag_configure(_NO_ROW, foreground=_GREY)
        ttk.Label(box, textvariable=self.last_announcement).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        return section

    def _build_template(self, parent: ttk.Frame) -> ttk.Frame:
        section, box = self._section(parent, _TEMPLATE_TITLE)
        box.columnconfigure(0, weight=1)
        entry = ttk.Entry(box, textvariable=self.template)
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda event: self.save_template())
        self.template.trace_add("write", lambda *args: self._show_template_button())
        self.save_template_button = ttk.Button(
            box, text="Kaydet", command=self.save_template
        )
        self.save_template_button.grid(row=0, column=1, padx=(8, 0))
        self.test_button = ttk.Button(box, text="Test", command=self.test)
        self.test_button.grid(row=0, column=2, padx=(8, 0))
        self._note(box, texts.TEMPLATE_HINT).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )
        return section

    def _build_announcements(self, parent: ttk.Frame) -> ttk.Frame:
        section, box = self._section(parent, "Özel Anonslar")
        box.columnconfigure(0, weight=1)
        self.announcements = self._table(
            box,
            height=3,
            columns={"time": "Saat", "days": "Günler", "text": "Anons"},
            widths={"time": 60, "days": 200, "text": 320},
        )
        self.announcements.tag_configure(_NO_ROW, foreground=_GREY)
        self.announcements.bind(
            "<<TreeviewSelect>>", lambda event: self._show_buttons()
        )
        self.announcements.bind("<Double-1>", lambda event: self.edit())
        self.announcements.bind("<Return>", lambda event: self.edit())
        self.announcements.bind("<Delete>", lambda event: self.delete())
        self.announcements.bind("<BackSpace>", lambda event: self.delete())
        buttons = ttk.Frame(box)
        buttons.grid(row=0, column=2, sticky="n", padx=(8, 0))
        self.add_button = ttk.Button(buttons, text="Ekle…", command=self.add)
        self.edit_button = ttk.Button(buttons, text="Düzenle…", command=self.edit)
        self.delete_button = ttk.Button(buttons, text="Sil", command=self.delete)
        for row, button in enumerate(
            (self.add_button, self.edit_button, self.delete_button)
        ):
            button.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        return section

    def _build_warnings(self, parent: ttk.Frame) -> tk.Frame:
        box = tk.Frame(parent, background=_WARNING_BACKGROUND, padx=10, pady=8)
        box.columnconfigure(0, weight=1)
        tk.Label(
            box,
            text="Uyarılar",
            font=self._bold,
            background=_WARNING_BACKGROUND,
            foreground=_WARNING_TEXT,
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
            background=_WARNING_BACKGROUND,
            foreground=_WARNING_TEXT,
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
        footer = ttk.Frame(parent)
        footer.columnconfigure(1, weight=1)
        self._note(footer, texts.SLEEP_NOTICE).grid(row=0, column=0, sticky="w")
        ttk.Label(footer, textvariable=self.activity).grid(
            row=0, column=1, sticky="e", padx=_PAD
        )
        self.log_button = ttk.Button(footer, text="Kayıtlar", command=self.open_log)
        self.log_button.grid(row=0, column=2)
        return footer

    @staticmethod
    def _table(
        parent: ttk.LabelFrame,
        height: int,
        columns: dict[str, str],
        widths: dict[str, int],
        select: bool = True,
    ) -> ttk.Treeview:
        """The "text" column takes up the width that is left over."""
        table = ttk.Treeview(
            parent,
            columns=tuple(columns),
            show="headings",
            height=height,
            selectmode="browse" if select else "none",
        )
        for name, heading in columns.items():
            table.heading(name, text=heading, anchor="w")
            table.column(
                name, width=widths[name], minwidth=widths[name], stretch=name == "text"
            )
        table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        table.configure(yscrollcommand=scrollbar.set)
        return table

    def _show_today(self, now: datetime.datetime) -> None:
        rows = self._app.rows()
        day = WEEKDAY_NAMES[now.weekday()]
        shown = [
            (day, f"{row.time:%H:%M}", row.summary, texts.STATUS[row.status])
            for row in rows
        ]
        if shown == self._shown_rows:
            return  # left alone, so that it keeps its place while scrolled
        self._shown_rows = shown
        self.today.delete(*self.today.get_children())
        for index, (row, values) in enumerate(zip(rows, shown, strict=True)):
            self.today.insert(
                "", "end", iid=str(index), values=values, tags=row.status.name
            )
        if not rows:
            self.today.insert(
                "", "end", iid=_NO_ROW, values=("", "", texts.NO_ROWS, ""), tags=_NO_ROW
            )
        current = [
            str(index)
            for index, row in enumerate(rows)
            if row.status in (Status.PLAYING, Status.NEXT)
        ]
        self._current_row = current[0] if current else None
        self._see_current()

    def _on_table_laid_out(self, event: tk.Event[ttk.Treeview]) -> None:
        """Only once the table has its size: a later resize must not undo what
        the operator scrolled to."""
        if event.height > 1:
            self.today.unbind("<Configure>")
            self._see_current()

    def _see_current(self) -> None:
        if self._current_row is not None:
            self.today.see(self._current_row)

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
        changed = self.template.get().strip() != self._app.settings.template
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
        if template == self._app.settings.template:
            return
        try:
            self._app.set_template(template)
        except ValueError:
            self._alert(_TEMPLATE_TITLE, texts.TEMPLATE_NEEDS_PLACEHOLDER)
        except OSError:
            _log.exception("Cannot save the template")
            self._alert(_TEMPLATE_TITLE, texts.CANNOT_SAVE)
        self.show()

    def test(self) -> None:
        try:
            self._app.test(self.template.get().strip())
        except ValueError:
            self._alert(_TEMPLATE_TITLE, texts.TEMPLATE_NEEDS_PLACEHOLDER)
        self.show()

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
