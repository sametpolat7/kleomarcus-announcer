import datetime
import logging
import tkinter as tk
from tkinter import ttk

from announcer.app import App, ChangeProblem
from announcer.models import ALL_WEEKDAYS, WEEKDAY_NAMES, CustomAnnouncement
from announcer.today import BUSY
from announcer.ui import texts
from announcer.ui.dialog import WRAP, Dialog, time_boxes

_log = logging.getLogger(__name__)

_FOLLOW = 250  # milliseconds


class AnnouncementDialog(Dialog):
    """``index`` is the place of the announcement to change among the app's
    custom announcements, or None to add a new one.
    """

    def __init__(self, parent: tk.Misc, app: App, index: int | None = None) -> None:
        existing = None if index is None else app.settings.custom_announcements[index]
        super().__init__(
            parent,
            "Özel Anons Ekle" if existing is None else "Özel Anonsu Düzenle",
            self.save,
        )
        self._app = app
        self._index = index

        time = existing.time if existing else datetime.time(12, 0)
        weekdays = existing.weekdays if existing else ALL_WEEKDAYS
        self.text = tk.StringVar(value=existing.text if existing else "")
        self.hour = tk.StringVar(value=f"{time.hour:02d}")
        self.minute = tk.StringVar(value=f"{time.minute:02d}")
        self.every_day = tk.BooleanVar(value=weekdays == ALL_WEEKDAYS)
        self.days = [tk.BooleanVar(value=day in weekdays) for day in ALL_WEEKDAYS]

        frame = self.frame
        ttk.Label(frame, text="Anons metni").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=self.text, width=60)
        entry.grid(row=1, column=0, sticky="ew", pady=(4, 14))

        ttk.Label(frame, text="Saat").grid(row=2, column=0, sticky="w")
        time_boxes(frame, self.hour, self.minute)[0].grid(
            row=3, column=0, sticky="w", pady=(4, 14)
        )

        ttk.Label(frame, text="Günler").grid(row=4, column=0, sticky="w")
        choice = ttk.Frame(frame)
        choice.grid(row=5, column=0, sticky="w", pady=(4, 4))
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
            box.grid(row=column // 4, column=column % 4, sticky="w", padx=(0, 14))
        self.show_days()

        # Empty until a test is asked for here.
        self.test_status = tk.StringVar()
        self._tested = False
        ttk.Label(frame, textvariable=self.test_status, wraplength=WRAP).grid(
            row=7, column=0, sticky="w", pady=(0, 8)
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=8, column=0, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        self.test_button = ttk.Button(buttons, text="Test", command=self.test)
        self.test_button.grid(row=0, column=0, sticky="w")
        ttk.Button(buttons, text="Vazgeç", command=self.window.destroy).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(
            buttons, text="Kaydet", style="Primary.TButton", command=self.save
        ).grid(row=0, column=2)
        entry.focus_set()

    def test(self) -> None:
        text = self.text.get().strip()
        if not text:
            self.alert(texts.CHANGE_PROBLEMS[ChangeProblem.NO_TEXT])
            return
        self._app.preview(text)
        if not self._tested:
            self._tested = True
            self._follow_test()

    def _follow_test(self) -> None:
        """The main window, which takes in what the app reports, keeps running
        underneath; this only reads the test's outcome.
        """
        if not self.window.winfo_exists():
            return
        status = self._app.test_status
        self.test_status.set(texts.activity(generating=False, test=status))
        # One test at a time: pressing again would only queue the same text.
        busy = status in BUSY
        self.test_button.state(["disabled" if busy else "!disabled"])
        self.window.after(_FOLLOW, self._follow_test)

    def save(self) -> None:
        announcement = self._entered()
        if announcement is None:
            return
        try:
            self._app.save_announcement(announcement, self._index)
        except OSError:
            _log.exception("Cannot save the custom announcement")
            self.alert(texts.CANNOT_SAVE)
        else:
            self.window.destroy()

    def _entered(self) -> CustomAnnouncement | None:
        text = self.text.get().strip()
        if not text:
            self.alert(texts.CHANGE_PROBLEMS[ChangeProblem.NO_TEXT])
            return None
        try:
            time = datetime.time(int(self.hour.get()), int(self.minute.get()))
        except ValueError:
            self.alert(texts.INVALID_TIME)
            return None
        weekdays = (
            ALL_WEEKDAYS
            if self.every_day.get()
            else frozenset(day for day in ALL_WEEKDAYS if self.days[day].get())
        )
        if not weekdays:
            self.alert("En az bir gün seçin.")
            return None
        return CustomAnnouncement(text, time, weekdays)

    def choose_days(self) -> None:
        if not self.every_day.get() and all(day.get() for day in self.days):
            # Start from nothing ticked: ticking two days beats unticking five.
            for day in self.days:
                day.set(False)
        self.show_days()

    def show_days(self) -> None:
        state = "disabled" if self.every_day.get() else "!disabled"
        for box in self.day_boxes:
            box.state([state])
