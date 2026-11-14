import tkinter as tk
from tkinter import ttk

from announcer.app import App, ChangeProblem
from announcer.ui import texts
from announcer.ui.dialog import WRAP, Dialog


class InstantDialog(Dialog):
    """Today's table shows how reading the sentence goes."""

    def __init__(self, parent: tk.Misc, app: App) -> None:
        super().__init__(parent, "Anlık Anons", self.read)
        self._app = app
        self.text = tk.StringVar()

        frame = self.frame
        ttk.Label(frame, text="Okunacak metin").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=self.text, width=64)
        entry.grid(row=1, column=0, sticky="ew", pady=(4, 6))
        ttk.Label(
            frame, text=texts.INSTANT_HINT, style="Muted.TLabel", wraplength=WRAP
        ).grid(row=2, column=0, sticky="w", pady=(0, 20))

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, sticky="e")
        ttk.Button(buttons, text="Vazgeç", command=self.window.destroy).grid(
            row=0, column=0, padx=(0, 8)
        )
        self.read_button = ttk.Button(
            buttons, text="Oku", style="Primary.TButton", command=self.read
        )
        self.read_button.grid(row=0, column=1)
        entry.focus_set()

    def read(self) -> None:
        try:
            self._app.announce(self.text.get())
        except ValueError:
            self.alert(texts.CHANGE_PROBLEMS[ChangeProblem.NO_TEXT])
            return
        self.window.destroy()
