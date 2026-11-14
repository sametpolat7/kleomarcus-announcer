import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from announcer.ui import theme

WRAP = 520


class Dialog:
    def __init__(
        self, parent: tk.Misc, title: str, on_return: Callable[[], None]
    ) -> None:
        self._parent = parent.winfo_toplevel()
        self._title = title
        self._on_return = on_return
        self.window = tk.Toplevel(parent, background=theme.CARD)
        theme.keep_light(self.window)
        self.window.title(title)
        self.window.resizable(False, False)
        self.window.transient(self._parent)
        self.frame = ttk.Frame(self.window, padding=20)
        self.frame.grid(sticky="nsew")
        self.frame.columnconfigure(0, weight=1)
        self.window.bind("<Return>", self._pressed_return)
        self.window.bind("<Escape>", lambda event: self.window.destroy())

    def wait(self) -> None:
        window, parent = self.window, self._parent
        window.withdraw()
        window.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - window.winfo_reqwidth()) // 2
        y = (
            parent.winfo_rooty()
            + (parent.winfo_height() - window.winfo_reqheight()) // 3
        )
        window.geometry(f"+{x}+{y}")
        window.deiconify()
        window.wait_visibility()
        window.grab_set()
        window.wait_window()

    def alert(self, message: str) -> None:
        messagebox.showerror(self._title, message, parent=self.window)

    def _pressed_return(self, event: object) -> str:
        """Have Return press the button that has the focus, as Space does."""
        focused = self.window.focus_get()
        if isinstance(focused, ttk.Button):
            focused.invoke()
        else:
            self._on_return()
        return "break"


def time_boxes(
    parent: tk.Misc, hour: tk.StringVar, minute: tk.StringVar
) -> tuple[ttk.Frame, list[ttk.Spinbox]]:
    frame = ttk.Frame(parent)
    boxes = [
        ttk.Spinbox(
            frame,
            textvariable=variable,
            from_=0,
            to=last,
            wrap=True,
            width=3,
            format="%02.0f",
            justify="center",
        )
        for variable, last in ((hour, 23), (minute, 59))
    ]
    boxes[0].grid(row=0, column=0)
    ttk.Label(frame, text=":").grid(row=0, column=1, padx=4)
    boxes[1].grid(row=0, column=2)
    return frame, boxes
