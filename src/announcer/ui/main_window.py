"""The main window: today's announcements and everything the operator can do."""

import datetime
import logging
import os
import subprocess
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from types import TracebackType

from announcer import APP_NAME
from announcer.app import App
from announcer.loader import ScheduleFileError
from announcer.today import Status
from announcer.ui import texts
from announcer.ui.announcement_dialog import AnnouncementDialog

_log = logging.getLogger(__name__)

_POLL = 250  # milliseconds between two looks at what the app has to report
_PAD = 12

# Statuses that mean an announcement was not heard, or not in full.
_PROBLEMS = frozenset(
    {
        Status.PARTLY_PLAYED,
        Status.NO_SPEECH,
        Status.ASLEEP,
        Status.NO_OUTPUT,
        Status.CONFLICT,
    }
)


def open_file(path: Path) -> None:
    """Show ``path`` in the program that the system opens such files with."""
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.run(["open", str(path)], check=False)


class MainWindow:
    """The window of the app. Everything here runs on the main thread.

    ``log_file`` is what the "Kayıtlar" button opens. ``clock`` tells the
    window when the minute or the day changes.
    """

    def __init__(
        self,
        app: App,
        log_file: Path,
        clock: Callable[[], datetime.datetime] = datetime.datetime.now,
    ) -> None:
        self._app = app
        self._log_file = log_file
        self._clock = clock
        self._shown_minute: datetime.datetime | None = None
        self._shown_rows: list[tuple[str, str, str, str]] | None = None
        self._shown_warnings: list[str] | None = None

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        # Small enough for a laptop screen; the tables take up the difference.
        height = min(740, self.root.winfo_screenheight() - 120)
        self.root.geometry(f"860x{height}")
        self.root.minsize(720, 600)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        # Tk prints an error in a button's command to a console that a
        # windowed app does not have.
        self.root.report_callback_exception = self._report_error

        self.schedule_path = tk.StringVar()
        self.refreshed = tk.StringVar()
        self.today_title = tk.StringVar()
        self.last_announcement = tk.StringVar()
        self.template = tk.StringVar(value=app.settings.template)
        self.activity = tk.StringVar()

        frame = ttk.Frame(self.root, padding=_PAD)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=3, minsize=150)
        frame.rowconfigure(3, weight=1, minsize=110)
        self._build_schedule(frame).grid(row=0, sticky="ew")
        self._build_today(frame).grid(row=1, sticky="nsew", pady=(_PAD, 0))
        self._build_template(frame).grid(row=2, sticky="ew", pady=(_PAD, 0))
        self._build_announcements(frame).grid(row=3, sticky="nsew", pady=(_PAD, 0))
        self._build_warnings(frame).grid(row=4, sticky="ew", pady=(_PAD, 0))
        self._build_footer(frame).grid(row=5, sticky="ew", pady=(_PAD, 0))

        self.show()
        self._after = self.root.after(_POLL, self._tick)

    def run(self) -> None:
        """Show the window until it is closed."""
        self.root.mainloop()

    def close(self) -> None:
        """Close the window."""
        self.root.after_cancel(self._after)
        self.root.destroy()

    def show(self) -> None:
        """Bring everything in the window up to date with the app."""
        app, now = self._app, self._clock()
        self._shown_minute = now.replace(second=0, microsecond=0)
        path = app.settings.schedule_path
        self.schedule_path.set("" if path is None else str(path))
        self.refreshed.set(texts.refreshed(app.refreshed_at))
        self.today_title.set(texts.today_title(now))
        self.last_announcement.set(texts.last_announcement(app.last_announcement))
        self.activity.set(texts.activity(app.generating, app.test_status))
        self._show_today()
        self._show_announcements()
        self._show_warnings()

    # Building the window

    def _build_schedule(self, parent: ttk.Frame) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Program Dosyası", padding=8)
        box.columnconfigure(0, weight=1)
        ttk.Entry(box, textvariable=self.schedule_path, state="readonly").grid(
            row=0, column=0, sticky="ew"
        )
        self.choose_button = ttk.Button(box, text="Dosya Seç…", command=self.choose)
        self.choose_button.grid(row=0, column=1, padx=(8, 0))
        self.refresh_button = ttk.Button(box, text="Yenile", command=self.refresh)
        self.refresh_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Label(box, textvariable=self.refreshed).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )
        return box

    def _build_today(self, parent: ttk.Frame) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Bugünün Anonsları", padding=8)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)
        ttk.Label(box, textvariable=self.today_title).grid(row=0, column=0, sticky="w")
        self.today = self._table(
            box,
            row=1,
            height=6,
            columns={"time": "Saat", "kind": "Tür", "text": "Anons", "status": "Durum"},
            widths={"time": 60, "kind": 70, "text": 380, "status": 150},
            stretch="text",
        )
        bold = ("TkDefaultFont", 0, "bold")
        self.today.tag_configure(Status.NEXT.name, font=bold)
        self.today.tag_configure(Status.PLAYING.name, font=bold)
        self.today.tag_configure(Status.PASSED.name, foreground="#808080")
        for status in _PROBLEMS:
            self.today.tag_configure(status.name, foreground="#c0392b")
        ttk.Label(box, textvariable=self.last_announcement).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        return box

    def _build_template(self, parent: ttk.Frame) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Anons Şablonu", padding=8)
        box.columnconfigure(0, weight=1)
        entry = ttk.Entry(box, textvariable=self.template)
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda event: self.save_template())
        self.save_template_button = ttk.Button(
            box, text="Kaydet", command=self.save_template
        )
        self.save_template_button.grid(row=0, column=1, padx=(8, 0))
        self.test_button = ttk.Button(box, text="Test", command=self.test)
        self.test_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Label(box, text=texts.TEMPLATE_HINT).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )
        return box

    def _build_announcements(self, parent: ttk.Frame) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Özel Anonslar", padding=8)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.announcements = self._table(
            box,
            row=0,
            height=3,
            columns={"time": "Saat", "days": "Günler", "text": "Anons"},
            widths={"time": 60, "days": 200, "text": 320},
            stretch="text",
        )
        self.announcements.bind(
            "<<TreeviewSelect>>", lambda event: self._show_buttons()
        )
        self.announcements.bind("<Double-1>", lambda event: self.edit())
        buttons = ttk.Frame(box)
        buttons.grid(row=0, column=2, sticky="n", padx=(8, 0))
        self.add_button = ttk.Button(buttons, text="Ekle…", command=self.add)
        self.edit_button = ttk.Button(buttons, text="Düzenle…", command=self.edit)
        self.delete_button = ttk.Button(buttons, text="Sil", command=self.delete)
        for row, button in enumerate(
            (self.add_button, self.edit_button, self.delete_button)
        ):
            button.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        return box

    def _build_warnings(self, parent: ttk.Frame) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Uyarılar", padding=8)
        box.columnconfigure(0, weight=1)
        self.warnings = tk.Text(
            box,
            height=3,
            wrap="word",
            state="disabled",
            relief="flat",
            highlightthickness=0,
            font="TkDefaultFont",
            background=ttk.Style().lookup("TFrame", "background") or "",
        )
        self.warnings.grid(row=0, column=0, sticky="ew")
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=self.warnings.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.warnings.configure(yscrollcommand=scrollbar.set)
        return box

    def _build_footer(self, parent: ttk.Frame) -> ttk.Frame:
        footer = ttk.Frame(parent)
        footer.columnconfigure(1, weight=1)
        ttk.Label(footer, text=texts.SLEEP_NOTICE).grid(row=0, column=0, sticky="w")
        ttk.Label(footer, textvariable=self.activity).grid(
            row=0, column=1, sticky="e", padx=_PAD
        )
        self.log_button = ttk.Button(footer, text="Kayıtlar", command=self.open_log)
        self.log_button.grid(row=0, column=2)
        return footer

    @staticmethod
    def _table(
        parent: ttk.LabelFrame,
        row: int,
        height: int,
        columns: dict[str, str],
        widths: dict[str, int],
        stretch: str,
    ) -> ttk.Treeview:
        """Return a table with a scrollbar, placed in ``row`` of ``parent``."""
        table = ttk.Treeview(
            parent,
            columns=tuple(columns),
            show="headings",
            height=height,
            selectmode="browse",
        )
        for name, heading in columns.items():
            table.heading(name, text=heading, anchor="w")
            table.column(
                name, width=widths[name], minwidth=widths[name], stretch=name == stretch
            )
        table.grid(row=row, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=table.yview)
        scrollbar.grid(row=row, column=1, sticky="ns")
        table.configure(yscrollcommand=scrollbar.set)
        return table

    # Showing the app's state

    def _show_today(self) -> None:
        rows = self._app.rows()
        shown = [
            (
                f"{row.time:%H:%M}",
                texts.kind(row.custom),
                row.summary,
                texts.STATUS[row.status],
            )
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
        # Bring what is sounding now, or comes next, into view.
        current = [
            index
            for index, row in enumerate(rows)
            if row.status in (Status.PLAYING, Status.NEXT)
        ]
        if current:
            self.today.see(str(current[0]))

    def _show_announcements(self) -> None:
        """List the custom announcements by time; a row's id is its index."""
        selected = self.announcements.selection()
        self.announcements.delete(*self.announcements.get_children())
        announcements = self._app.settings.custom_announcements
        for index in sorted(
            range(len(announcements)), key=lambda index: announcements[index].time
        ):
            item = announcements[index]
            self.announcements.insert(
                "",
                "end",
                iid=str(index),
                values=(f"{item.time:%H:%M}", texts.days(item.weekdays), item.text),
            )
        if selected and self.announcements.exists(selected[0]):
            self.announcements.selection_set(selected[0])
        self._show_buttons()

    def _show_buttons(self) -> None:
        """Let an announcement be changed or deleted only while one is selected."""
        state = "!disabled" if self.announcements.selection() else "disabled"
        self.edit_button.state([state])
        self.delete_button.state([state])

    def _show_warnings(self) -> None:
        lines = texts.warnings(self._app)
        if lines == self._shown_warnings:
            return
        self._shown_warnings = lines
        self.warnings.configure(state="normal")
        self.warnings.delete("1.0", "end")
        self.warnings.insert("1.0", "\n".join(f"• {line}" for line in lines))
        if not lines:
            self.warnings.insert("1.0", texts.NO_WARNINGS)
        self.warnings.configure(state="disabled")

    def _tick(self) -> None:
        """Take in what the app has to report, and follow the clock."""
        changed = self._app.poll()
        minute = self._clock().replace(second=0, microsecond=0)
        if changed or minute != self._shown_minute:
            self.show()
        self._after = self.root.after(_POLL, self._tick)

    # What the operator can do

    def choose(self) -> None:
        """Ask for a schedule file and use it."""
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="Program Dosyasını Seç",
            filetypes=[("Excel dosyası", "*.xlsx")],
        )
        if not chosen:
            return
        try:
            self._app.choose_schedule(Path(chosen))
        except ScheduleFileError as error:
            _log.warning("Cannot use %s: %s (%r)", chosen, error, error.__cause__)
            self._alert("Program Dosyası", texts.schedule_error(error))
        except OSError:
            _log.exception("Cannot save the choice of schedule file")
            self._alert("Program Dosyası", texts.CANNOT_SAVE)
        self.show()

    def refresh(self) -> None:
        """Read the schedule file again."""
        _log.info("Refresh requested")
        self._app.refresh()
        self.show()

    def save_template(self) -> None:
        """Store the template as entered."""
        template = self.template.get().strip()
        if template == self._app.settings.template:
            return
        try:
            self._app.set_template(template)
        except ValueError:
            self._alert("Anons Şablonu", texts.TEMPLATE_NEEDS_PLACEHOLDER)
        except OSError:
            _log.exception("Cannot save the template")
            self._alert("Anons Şablonu", texts.CANNOT_SAVE)
        self.show()

    def test(self) -> None:
        """Announce the template as entered, saved or not, with a made-up name."""
        try:
            self._app.test(self.template.get().strip())
        except ValueError:
            self._alert("Anons Şablonu", texts.TEMPLATE_NEEDS_PLACEHOLDER)
        self.show()

    def add(self) -> None:
        """Ask for a new custom announcement."""
        AnnouncementDialog(self.root, self._app).wait()
        self.show()

    def edit(self) -> None:
        """Ask for changes to the selected custom announcement."""
        selected = self.announcements.selection()
        if selected:
            AnnouncementDialog(self.root, self._app, int(selected[0])).wait()
            self.show()

    def delete(self) -> None:
        """Delete the selected custom announcement, after asking."""
        selected = self.announcements.selection()
        if not selected:
            return
        announcement = self._app.settings.custom_announcements[int(selected[0])]
        if not messagebox.askyesno(
            "Özel Anonsu Sil",
            f"“{announcement.text}” anonsu silinsin mi?",
            parent=self.root,
        ):
            return
        try:
            self._app.delete_announcement(int(selected[0]))
        except OSError:
            _log.exception("Cannot save the deletion of a custom announcement")
            self._alert("Özel Anonslar", texts.CANNOT_SAVE)
        self.announcements.selection_set()
        self.show()

    def open_log(self) -> None:
        """Show the log file."""
        open_file(self._log_file)

    def _alert(self, title: str, message: str) -> None:
        messagebox.showerror(title, message, parent=self.root)

    def _report_error(
        self,
        kind: type[BaseException],
        error: BaseException,
        trace: TracebackType | None,
    ) -> None:
        """Log an error that an action in the window did not handle, and say so."""
        _log.error("An action in the window failed", exc_info=(kind, error, trace))
        self._alert(
            APP_NAME, "Beklenmeyen bir hata oluştu. Ayrıntılar kayıt dosyasındadır."
        )
