"""Run with ``uv run scripts/make_logo.py`` after changing the drawing in
``announcer.logo``.
"""

from pathlib import Path

from announcer.logo import ICON_SIZES, icon_path, render_png, render_svg

SVG_PATH = Path(__file__).resolve().parent.parent / "docs" / "logo.svg"


def main() -> None:
    SVG_PATH.write_text(render_svg(), encoding="utf-8", newline="\n")
    print(f"Wrote {SVG_PATH}")
    for size in ICON_SIZES:
        icon_path(size).write_bytes(render_png(size))
        print(f"Wrote {icon_path(size)}")


if __name__ == "__main__":
    main()
