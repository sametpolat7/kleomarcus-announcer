"""The dialog that adds or changes a custom announcement."""

import datetime
import logging
import tkinter as tk
from tkinter import messagebox, ttk

from announcer.app import App, SlotTakenError
from announcer.models import ALL_WEEKDAYS, WEEKDAY_NAMES, CustomAnnouncement
from announcer.ui import texts

_log = logging.getLogger(__name__)

_NO_TEXT = "Anons metni boş olamaz."


class AnnouncementDialog:
    """A window over the main one with the text, time and days of an announcement.

    ``index`` is the place of the announcement to change among the app's
    custom announcements, or None to add a new one. The dialog saves through
    the app, and stays open while what was entered cannot be saved.
    """

    def __init__(self, parent: tk.Misc, app: App, index: int | None = None) -> None:
        self._app = app
        self._index = index
        existing = None if index is None else app.settings.custom_announcements[index]

        self.window = tk.Toplevel(parent)
        self.window.title(
            "Özel Anons Ekle" if existing is None else "Özel Anonsu Düzenle"
        )
        self.window.resizable(False, False)
        self.window.transient(parent.winfo_toplevel())
        self.window.geometry(
            f"+{parent.winfo_rootx() + 60}+{parent.winfo_rooty() + 60}"
        )

        time = existing.time if existing else datetime.time(12, 0)
        weekdays = existing.weekdays if existing else ALL_WEEKDAYS
        self.text = tk.StringVar(value=existing.text if existing else "")
        self.hour = tk.StringVar(value=f"{time.hour:02d}")
        self.minute = tk.StringVar(value=f"{time.minute:02d}")
        self.every_day = tk.BooleanVar(value=weekdays == ALL_WEEKDAYS)
        self.days = [tk.BooleanVar(value=day in weekdays) for day in ALL_WEEKDAYS]

        frame = ttk.Frame(self.window, padding=16)
        frame.grid(sticky="nsew")

        ttk.Label(frame, text="Anons metni").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=self.text, width=60)
        entry.grid(row=1, column=0, sticky="ew", pady=(2, 12))

        ttk.Label(frame, text="Saat").grid(row=2, column=0, sticky="w")
        clock = ttk.Frame(frame)
        clock.grid(row=3, column=0, sticky="w", pady=(2, 12))
        for column, (variable, last) in enumerate(((self.hour, 23), (self.minute, 59))):
            if column:
                ttk.Label(clock, text=":").grid(row=0, column=1, padx=4)
            ttk.Spinbox(
                clock,
                textvariable=variable,
                from_=0,
                to=last,
                wrap=True,
                width=3,
                format="%02.0f",
                justify="center",
            ).grid(row=0, column=column * 2)

        ttk.Label(frame, text="Günler").grid(row=4, column=0, sticky="w")
        choice = ttk.Frame(frame)
        choice.grid(row=5, column=0, sticky="w", pady=(2, 4))
        for column, (label, value) in enumerate(
            (("Her gün", True), ("Seçili günler", False))
        ):
            ttk.Radiobutton(
                choice,
                text=label,
                variable=self.every_day,
                value=value,
                command=self.choose_days,
            ).grid(row=0, column=column, padx=(0, 16))
        week = ttk.Frame(frame)
        week.grid(row=6, column=0, sticky="w", pady=(0, 16))
        self.day_boxes = [
            ttk.Checkbutton(week, text=name, variable=variable)
            for name, variable in zip(WEEKDAY_NAMES, self.days, strict=True)
        ]
        for column, box in enumerate(self.day_boxes):
            box.grid(row=column // 4, column=column % 4, sticky="w", padx=(0, 12))
        self.show_days()

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        ttk.Button(buttons, text="Test", command=self.test).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(buttons, text="Vazgeç", command=self.window.destroy).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(buttons, text="Kaydet", command=self.save, default="active").grid(
            row=0, column=2
        )

        self.window.bind("<Return>", lambda event: self.save())
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        entry.focus_set()

    def wait(self) -> None:
        """Keep the main window out of reach until the dialog is closed."""
        self.window.wait_visibility()
        self.window.grab_set()
        self.window.wait_window()

    def test(self) -> None:
        """Announce the text as entered, saved or not, so it can be heard first."""
        text = self.text.get().strip()
        if text:
            self._app.preview(text)
        else:
            self._alert(_NO_TEXT)

    def save(self) -> None:
        """Save what was entered and close; say why if it cannot be saved."""
        announcement = self._entered()
        if announcement is None:
            return
        try:
            self._app.save_announcement(announcement, self._index)
        except SlotTakenError as error:
            self._alert(texts.conflict_alert(error.conflicts))
        except OSError:
            _log.exception("Cannot save the custom announcement")
            self._alert(texts.CANNOT_SAVE)
        else:
            self.window.destroy()

    def _entered(self) -> CustomAnnouncement | None:
        """Return the announcement as entered; say why if it is not valid."""
        text = self.text.get().strip()
        if not text:
            self._alert(_NO_TEXT)
            return None
        try:
            time = datetime.time(int(self.hour.get()), int(self.minute.get()))
        except ValueError:
            self._alert("Saat 00:00 ile 23:59 arasında olmalıdır.")
            return None
        weekdays = (
            ALL_WEEKDAYS
            if self.every_day.get()
            else frozenset(day for day in ALL_WEEKDAYS if self.days[day].get())
        )
        if not weekdays:
            self._alert("En az bir gün seçin.")
            return None
        return CustomAnnouncement(text, time, weekdays)

    def choose_days(self) -> None:
        """Follow a switch between every day and chosen days."""
        if not self.every_day.get() and all(day.get() for day in self.days):
            # Start from nothing ticked: ticking two days beats unticking five.
            for day in self.days:
                day.set(False)
        self.show_days()

    def show_days(self) -> None:
        """Let the weekdays be ticked only when the choice is not every day."""
        state = "disabled" if self.every_day.get() else "!disabled"
        for box in self.day_boxes:
            box.state([state])

    def _alert(self, message: str) -> None:
        messagebox.showerror("Özel Anons", message, parent=self.window)
