import datetime
import logging
import tkinter as tk
from tkinter import ttk

from announcer.app import App, ChangeError, ChangeProblem, Editing, SpeechRequest
from announcer.today import BUSY, Row
from announcer.ui import texts
from announcer.ui.dialog import WRAP, Dialog, time_boxes

_log = logging.getLogger(__name__)

_FOLLOW = 250  # milliseconds


class ChangeDialog(Dialog):
    """Changes one row of today's table for today only."""

    def __init__(self, parent: tk.Misc, app: App, row: Row, editing: Editing) -> None:
        super().__init__(parent, "Bugünkü Anonsu Düzenle", self.save)
        self._app = app
        self._row = row
        self._editing = editing

        self.hour = tk.StringVar(value=f"{editing.time.hour:02d}")
        self.minute = tk.StringVar(value=f"{editing.time.minute:02d}")
        self.text = tk.StringVar(value=editing.text)

        frame = self.frame
        heading = [texts.ANNOUNCEMENT_OF[editing.kind]]
        if editing.name:
            heading.append(editing.name)
        heading.append(f"planlanan saat {editing.planned:%H:%M}")
        ttk.Label(frame, text=" · ".join(heading), style="Bold.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            frame, text=texts.TODAY_ONLY, style="Muted.TLabel", wraplength=WRAP
        ).grid(row=1, column=0, sticky="w", pady=(4, 16))

        ttk.Label(frame, text="Saat").grid(row=2, column=0, sticky="w")
        clock, boxes = time_boxes(frame, self.hour, self.minute)
        clock.grid(row=3, column=0, sticky="w", pady=(4, 14))
        ttk.Label(clock, text=texts.TIME_NOT_DELAYED, style="Muted.TLabel").grid(
            row=0, column=3, padx=(16, 0)
        )

        ttk.Label(frame, text="Anons metni").grid(row=4, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=self.text, width=64)
        entry.grid(row=5, column=0, sticky="ew", pady=(4, 6))
        if editing.end is not None:
            ttk.Label(
                frame,
                text=texts.end_stays(editing.end),
                style="Muted.TLabel",
                wraplength=WRAP,
            ).grid(row=6, column=0, sticky="w")

        # How a test of the text, or the speech of a new text, is going;
        # empty until one is asked for here.
        self.test_status = tk.StringVar()
        self._tested = False
        self._speech: SpeechRequest | None = None  # of the text being saved
        ttk.Label(frame, textvariable=self.test_status, wraplength=WRAP).grid(
            row=7, column=0, sticky="w", pady=(12, 8)
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=8, column=0, sticky="ew")
        buttons.columnconfigure(1, weight=1)
        self.test_button = ttk.Button(buttons, text="Test", command=self.test)
        self.test_button.grid(row=0, column=0, sticky="w")
        self.reset_button = ttk.Button(buttons, text="Plana Dön", command=self.reset)
        self.reset_button.grid(row=0, column=2, padx=(0, 8))
        ttk.Button(buttons, text="Vazgeç", command=self.window.destroy).grid(
            row=0, column=3, padx=(0, 8)
        )
        self.save_button = ttk.Button(
            buttons, text="Kaydet", style="Primary.TButton", command=self.save
        )
        self.save_button.grid(row=0, column=4)
        self._inputs: list[ttk.Widget] = [
            *boxes,
            entry,
            self.save_button,
            self.test_button,
            self.reset_button,
        ]
        entry.focus_set()

    def reset(self) -> None:
        planned = self._editing.planned
        self.hour.set(f"{planned.hour:02d}")
        self.minute.set(f"{planned.minute:02d}")
        self.text.set(self._editing.planned_text)

    def test(self) -> None:
        text = self.text.get().strip()
        if not text:
            self.alert(texts.CHANGE_PROBLEMS[ChangeProblem.NO_TEXT])
            return
        self._app.preview(self._app.text_of(self._editing, text))
        if not self._tested:
            self._tested = True
            self._follow_test()

    def _follow_test(self) -> None:
        if not self.window.winfo_exists():
            return
        if self._speech is None:
            status = self._app.test_status
            self.test_status.set(texts.activity(generating=False, test=status))
            busy = status in BUSY
            self.test_button.state(["disabled" if busy else "!disabled"])
        self.window.after(_FOLLOW, self._follow_test)

    def save(self) -> None:
        """A new text is saved once its speech is there, so that it can be read."""
        if self._speech is not None:
            return
        try:
            time = datetime.time(int(self.hour.get()), int(self.minute.get()))
        except ValueError:
            self.alert(texts.INVALID_TIME)
            return
        text = self.text.get().strip()
        said = self._app.text_of(self._editing, text)
        if text and said != self._editing.planned_text:
            self._speech = self._app.make_speech([said])
            self.test_status.set(texts.SPEECH_PREPARING)
            self._hold(True)
            self._finish(time, text)
            return
        self._commit(time, text)

    def _finish(self, time: datetime.time, text: str) -> None:
        if not self.window.winfo_exists():
            return
        speech = self._speech
        if speech is not None and not speech.done:
            self.window.after(_FOLLOW, lambda: self._finish(time, text))
            return
        self._speech = None
        self.test_status.set("")
        self._hold(False)
        self._commit(time, text)

    def _hold(self, held: bool) -> None:
        """Keep what is being saved from being changed while its speech is made."""
        for widget in self._inputs:
            widget.state(["disabled" if held else "!disabled"])

    def _commit(self, time: datetime.time, text: str) -> None:
        try:
            self._app.edit(self._row, time, text)
        except ChangeError as error:
            self.alert(texts.CHANGE_PROBLEMS[error.problem])
            if error.problem is ChangeProblem.GONE:
                self.window.destroy()
        except OSError:
            _log.exception("Cannot save the change of an announcement")
            self.alert(texts.CANNOT_SAVE)
        else:
            self.window.destroy()
