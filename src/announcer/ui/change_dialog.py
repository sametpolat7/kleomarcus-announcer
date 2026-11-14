import datetime
import logging
import tkinter as tk
from tkinter import font, messagebox, ttk

from announcer.app import App, ChangeError, ChangeProblem, Editing, SpeechRequest
from announcer.today import BUSY, Row
from announcer.ui import texts
from announcer.ui.keys import bind_return

_log = logging.getLogger(__name__)

_TITLE = "Bugünkü Anonsu Düzenle"
_FOLLOW = 250  # milliseconds
_GREY = "#808080"


class ChangeDialog:
    """Changes one row of today's table for today only."""

    def __init__(self, parent: tk.Misc, app: App, row: Row, editing: Editing) -> None:
        self._app = app
        self._row = row
        self._editing = editing

        self.window = tk.Toplevel(parent)
        self.window.title(_TITLE)
        self.window.resizable(False, False)
        self.window.transient(parent.winfo_toplevel())
        self.window.geometry(
            f"+{parent.winfo_rootx() + 80}+{parent.winfo_rooty() + 80}"
        )

        self.hour = tk.StringVar(value=f"{editing.time.hour:02d}")
        self.minute = tk.StringVar(value=f"{editing.time.minute:02d}")
        self.text = tk.StringVar(value=editing.text)

        frame = ttk.Frame(self.window, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)

        # Kept on the dialog, as tkinter drops a font that nothing refers to.
        self._bold = font.nametofont("TkDefaultFont").copy()
        self._bold.configure(weight="bold")
        heading = [texts.ANNOUNCEMENT_OF[editing.kind]]
        if editing.name:
            heading.append(editing.name)
        heading.append(f"planlanan saat {editing.planned:%H:%M}")
        ttk.Label(frame, text=" · ".join(heading), font=self._bold).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(frame, text=texts.TODAY_ONLY, foreground=_GREY, wraplength=520).grid(
            row=1, column=0, sticky="w", pady=(4, 14)
        )

        ttk.Label(frame, text="Saat").grid(row=2, column=0, sticky="w")
        clock = ttk.Frame(frame)
        clock.grid(row=3, column=0, sticky="w", pady=(2, 12))
        ttk.Label(clock, text=texts.TIME_NOT_DELAYED, foreground=_GREY).grid(
            row=0, column=3, padx=(16, 0)
        )
        self._inputs: list[ttk.Widget] = []
        for column, (variable, last) in enumerate(((self.hour, 23), (self.minute, 59))):
            if column:
                ttk.Label(clock, text=":").grid(row=0, column=1, padx=4)
            spinbox = ttk.Spinbox(
                clock,
                textvariable=variable,
                from_=0,
                to=last,
                wrap=True,
                width=3,
                format="%02.0f",
                justify="center",
            )
            spinbox.grid(row=0, column=column * 2)
            self._inputs.append(spinbox)

        ttk.Label(frame, text="Anons metni").grid(row=4, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=self.text, width=64)
        entry.grid(row=5, column=0, sticky="ew", pady=(2, 4))
        self._inputs.append(entry)
        if editing.end is not None:
            ttk.Label(
                frame,
                text=texts.end_stays(editing.end),
                foreground=_GREY,
                wraplength=520,
            ).grid(row=6, column=0, sticky="w")

        # How a test of the text, or the speech of a new text, is going;
        # empty until one is asked for here.
        self.test_status = tk.StringVar()
        self._tested = False
        self._speech: SpeechRequest | None = None  # of the text being saved
        ttk.Label(frame, textvariable=self.test_status, wraplength=520).grid(
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
            buttons, text="Kaydet", command=self.save, default="active"
        )
        self.save_button.grid(row=0, column=4)
        self._inputs += [self.save_button, self.test_button, self.reset_button]

        bind_return(self.window, self.save)
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        entry.focus_set()

    def wait(self) -> None:
        self.window.wait_visibility()
        self.window.grab_set()
        self.window.wait_window()

    def reset(self) -> None:
        planned = self._editing.planned
        self.hour.set(f"{planned.hour:02d}")
        self.minute.set(f"{planned.minute:02d}")
        self.text.set(self._editing.planned_text)

    def test(self) -> None:
        text = self.text.get().strip()
        if not text:
            self._alert(texts.CHANGE_PROBLEMS[ChangeProblem.NO_TEXT])
            return
        self._app.preview(self._app.texts_of(self._editing, text)[0])
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
            self._alert("Saat 00:00 ile 23:59 arasında olmalıdır.")
            return
        text = self.text.get().strip()
        said = self._app.texts_of(self._editing, text)[0]
        if text and said != self._editing.planned_text:
            self._speech = self._app.make_speech(
                self._app.texts_of(self._editing, text)
            )
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
            self._alert(texts.CHANGE_PROBLEMS[error.problem])
            if error.problem is ChangeProblem.GONE:
                self.window.destroy()
        except OSError:
            _log.exception("Cannot save the change of an announcement")
            self._alert(texts.CANNOT_SAVE)
        else:
            self.window.destroy()

    def _alert(self, message: str) -> None:
        messagebox.showerror(_TITLE, message, parent=self.window)
