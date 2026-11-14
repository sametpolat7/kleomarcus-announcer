import tkinter as tk
from collections.abc import Callable
from tkinter import ttk


def bind_return(window: tk.Toplevel, action: Callable[[], None]) -> None:
    """Have Return press the button that has the focus, as Space does."""

    def pressed(event: object) -> str:
        focused = window.focus_get()
        if isinstance(focused, ttk.Button):
            focused.invoke()
        else:
            action()
        return "break"

    window.bind("<Return>", pressed)
