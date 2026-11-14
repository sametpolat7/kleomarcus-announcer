import tkinter as tk
from tkinter import font, ttk

WINDOW = "#f3f4f6"
CARD = "#ffffff"
BORDER = "#d9dde3"
TEXT = "#18181b"
MUTED = "#6b7280"
INK = "#18181b"
BLUE = "#1d4ed8"
RED = "#c62828"
WARNING_BACKGROUND = "#fff6d9"
WARNING_BORDER = "#ecd58b"
WARNING_TEXT = "#4d3b00"
POINTER = "hand2"

_FAINT = "#a3a9b3"
_HOVER = "#f3f4f6"
_PRESSED = "#e5e7eb"
_INK_HOVER = "#3a3a40"
_FIELD_BORDER = "#c3c8d0"
_SELECTED = "#e2ebfc"
_HEADING = "#f7f8fa"
_THUMB = "#c9ced6"
_ROW_PADDING = 6
_RESTYLED = (
    ".",
    "TButton",
    "TEntry",
    "TCombobox",
    "TSpinbox",
    "TCheckbutton",
    "TRadiobutton",
    "Treeview",
)
_PRESSABLE = ("TButton", "TCheckbutton", "TRadiobutton")
_POINT_UNLESS_DISABLED = (
    "+apply {{widget pointer} {"
    " if {![winfo exists $widget]} return;"
    ' set cursor [expr {[$widget instate disabled] ? "" : $pointer}];'
    " if {[$widget cget -cursor] ne $cursor} {$widget configure -cursor $cursor}"
    f"}}}} %W {POINTER}"
)


def describe(
    root: tk.Misc, by: int = 0, *styles: str
) -> tuple[str, int, *tuple[str, ...]]:
    base = font.nametofont("TkDefaultFont", root)
    size = int(base.cget("size"))
    return (str(base.cget("family")), size + by if size > 0 else size - by, *styles)


def apply(root: tk.Tk) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")
    for name in _RESTYLED:
        for option in style.map(name):
            style.tk.call("ttk::style", "map", name, f"-{option}", "")
    root.configure(background=WINDOW)
    keep_light(root)
    for pressable in _PRESSABLE:
        for moment in ("<Motion>", "<ButtonRelease-1>"):
            root.tk.call("bind", pressable, moment, _POINT_UNLESS_DISABLED)

    style.configure(
        ".",
        background=CARD,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=CARD,
        darkcolor=CARD,
        troughcolor=CARD,
        focuscolor=CARD,
        selectbackground=_SELECTED,
        selectforeground=TEXT,
        insertcolor=TEXT,
    )
    style.map(".", foreground=[("disabled", _FAINT)])

    style.configure("Window.TFrame", background=WINDOW)
    style.configure("Window.TLabel", background=WINDOW)
    style.configure("Heading.TLabel", font=describe(root, 6, "bold"))
    style.configure("Title.TLabel", font=describe(root, 2, "bold"))
    style.configure("Bold.TLabel", font=describe(root, 0, "bold"))
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Note.TLabel", foreground=MUTED, font=describe(root, -1))
    style.configure(
        "Window.Note.TLabel",
        background=WINDOW,
        foreground=MUTED,
        font=describe(root, -1),
    )

    face = [("disabled", CARD), ("pressed", _PRESSED), ("active", _HOVER)]
    style.configure(
        "TButton",
        padding=(12, 5),
        width=0,
        bordercolor=_FIELD_BORDER,
        anchor="center",
    )
    style.map(
        "TButton",
        background=face,
        lightcolor=face,
        darkcolor=face,
        bordercolor=[("disabled", BORDER), ("focus", MUTED)],
    )
    fill = [("disabled", _PRESSED), ("pressed", _INK_HOVER), ("active", _INK_HOVER)]
    style.configure(
        "Primary.TButton",
        background=INK,
        foreground=CARD,
        bordercolor=INK,
        lightcolor=INK,
        darkcolor=INK,
    )
    style.map(
        "Primary.TButton",
        background=fill,
        lightcolor=fill,
        darkcolor=fill,
        bordercolor=[fill[0], ("focus", BLUE), *fill[1:]],
    )
    style.configure("Danger.TButton", foreground=RED)

    style.element_create(
        "Plain.Combobox.downarrow",
        "image",
        _arrowhead(root, "announcer-down", (9, 7, 5, 3, 1)),
        sticky="",
        padding=(8, 0),
    )
    for name, rows, sticky in (
        ("uparrow", (1, 3, 5, 7, 0), "s"),
        ("downarrow", (0, 7, 5, 3, 1), "n"),
    ):
        style.element_create(
            f"Plain.Spinbox.{name}",
            "image",
            _arrowhead(root, f"announcer-small-{name}", rows),
            sticky=sticky,
            width=19,
            height=11,
        )
    _lay_out(
        style,
        "TCombobox",
        "Combobox.field -sticky nswe -children {"
        "Plain.Combobox.downarrow -side right -sticky ns "
        "Combobox.padding -sticky nswe -children {Combobox.textarea -sticky nswe}}",
    )
    _lay_out(
        style,
        "TSpinbox",
        "Spinbox.field -side top -sticky we -children {"
        "null -side right -sticky {} -children {"
        "Plain.Spinbox.uparrow -side top -sticky e "
        "Plain.Spinbox.downarrow -side bottom -sticky e} "
        "Spinbox.padding -sticky nswe -children {Spinbox.textarea -sticky nswe}}",
    )
    for field in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(
            field,
            padding=(6, 5),
            fieldbackground=CARD,
            bordercolor=_FIELD_BORDER,
        )
        style.map(
            field,
            foreground=[("disabled", _FAINT)],
            fieldbackground=[("disabled", WINDOW)],
            bordercolor=[("disabled", BORDER), ("focus", BLUE)],
            lightcolor=[("disabled", WINDOW), ("focus", BLUE)],
        )
    style.map(
        "TCombobox",
        selectbackground=[("readonly", CARD)],
        selectforeground=[("readonly", TEXT)],
    )
    style.configure("Shown.TEntry", fieldbackground=_HEADING)
    for option, value in (
        ("background", CARD),
        ("foreground", TEXT),
        ("selectBackground", _SELECTED),
        ("selectForeground", TEXT),
        ("borderWidth", 0),
    ):
        root.option_add(f"*TCombobox*Listbox.{option}", value)

    for kind in ("Checkbutton", "Radiobutton"):
        choice = f"T{kind}"
        style.element_create(
            f"Plain.{kind}.indicator", "from", "default", f"{kind}.indicator"
        )
        _lay_out(
            style,
            choice,
            f"{kind}.padding -sticky nswe -children {{"
            f"Plain.{kind}.indicator -side left -sticky {{}} "
            f"{kind}.focus -side left -sticky w -children {{"
            f"{kind}.label -sticky nswe}}}}",
        )
        style.configure(
            choice,
            padding=(0, 3),
            indicatorbackground=CARD,
            indicatorforeground=CARD,
            bordercolor=_FIELD_BORDER,
            indicatormargin=(0, 0, 6, 0),
            focuscolor=_FIELD_BORDER,
            focussolid=True,
        )
        style.map(
            choice,
            indicatorbackground=[
                ("disabled", "selected", _FAINT),
                ("disabled", WINDOW),
                ("selected", INK),
            ],
            bordercolor=[
                ("disabled", "selected", _FAINT),
                ("disabled", BORDER),
                ("selected", INK),
            ],
        )

    linespace = font.nametofont("TkDefaultFont", root).metrics("linespace")
    style.configure(
        "Treeview",
        background=CARD,
        fieldbackground=CARD,
        rowheight=linespace + _ROW_PADDING,
    )
    _lay_out(style, "Treeview", "Treeview.treearea -sticky nswe")
    style.configure("Treeview.Cell", padding=(10, 0))
    style.map("Treeview", background=[("selected", _SELECTED)])
    style.configure(
        "Treeview.Heading",
        background=_HEADING,
        foreground=MUTED,
        font=describe(root, 0, "bold"),
        relief="flat",
        padding=(8, 5),
    )

    _lay_out(
        style,
        "Vertical.TScrollbar",
        "Vertical.Scrollbar.trough -sticky ns -children {"
        "Vertical.Scrollbar.thumb -sticky nswe}",
    )
    thumb = [("active", _FAINT)]
    style.configure(
        "Vertical.TScrollbar",
        background=_THUMB,
        bordercolor=CARD,
        lightcolor=_THUMB,
        darkcolor=_THUMB,
        gripsize=0,
        arrowsize=11,
    )
    style.map(
        "Vertical.TScrollbar", background=thumb, lightcolor=thumb, darkcolor=thumb
    )


def _lay_out(style: ttk.Style, name: str, elements: str) -> None:
    style.tk.call("ttk::style", "layout", name, elements)


def _arrowhead(root: tk.Tk, name: str, rows: tuple[int, ...]) -> str:
    width = max(rows)
    root.tk.call(
        "image", "create", "photo", name, "-width", width, "-height", len(rows)
    )
    for y, across in enumerate(rows):
        left = (width - across) // 2
        if across:
            root.tk.call(name, "put", MUTED, "-to", left, y, left + across, y + 1)
    return name


def keep_light(window: tk.Tk | tk.Toplevel) -> None:
    if window.tk.call("tk", "windowingsystem") == "aqua":
        window.tk.call("wm", "attributes", window, "-appearance", "aqua")


def card(parent: tk.Misc) -> tk.Frame:
    return tk.Frame(
        parent,
        background=CARD,
        highlightbackground=BORDER,
        highlightthickness=1,
        padx=14,
        pady=12,
    )
