import tkinter as tk
from tkinter import messagebox, ttk

from announcer.app import App, ChangeProblem
from announcer.ui import texts
from announcer.ui.keys import bind_return

_TITLE = "Anlık Anons"
_GREY = "#808080"


class InstantDialog:
    """Today's table shows how reading the sentence goes."""

    def __init__(self, parent: tk.Misc, app: App) -> None:
        self._app = app

        self.window = tk.Toplevel(parent)
        self.window.title(_TITLE)
        self.window.resizable(False, False)
        self.window.transient(parent.winfo_toplevel())
        self.window.geometry(
            f"+{parent.winfo_rootx() + 80}+{parent.winfo_rooty() + 80}"
        )

        self.text = tk.StringVar()
        frame = ttk.Frame(self.window, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="Okunacak metin").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=self.text, width=64)
        entry.grid(row=1, column=0, sticky="ew", pady=(2, 4))
        ttk.Label(
            frame, text=texts.INSTANT_HINT, foreground=_GREY, wraplength=520
        ).grid(row=2, column=0, sticky="w", pady=(0, 16))

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, sticky="e")
        ttk.Button(buttons, text="Vazgeç", command=self.window.destroy).grid(
            row=0, column=0, padx=(0, 8)
        )
        self.read_button = ttk.Button(
            buttons, text="Oku", command=self.read, default="active"
        )
        self.read_button.grid(row=0, column=1)

        bind_return(self.window, self.read)
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        entry.focus_set()

    def wait(self) -> None:
        self.window.wait_visibility()
        self.window.grab_set()
        self.window.wait_window()

    def read(self) -> None:
        try:
            self._app.announce(self.text.get())
        except ValueError:
            messagebox.showerror(
                _TITLE,
                texts.CHANGE_PROBLEMS[ChangeProblem.NO_TEXT],
                parent=self.window,
            )
            return
        self.window.destroy()
