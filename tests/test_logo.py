"""Tests for the drawn logo, the bundled copies of it and the window's icon."""

import struct
import zlib
from pathlib import Path
from tkinter import messagebox

import pytest

from announcer.logo import ICON_SIZES, icon_path, render_png, render_svg
from announcer.ui import main_window

SVG_PATH = Path(__file__).parent.parent / "docs" / "logo.svg"


def pixels(png: bytes) -> tuple[bytes, bytes]:
    """Return the header and the pixel data of a PNG image.

    The compressed bytes differ between builds of zlib, so images are
    compared by what they hold.
    """
    chunks: dict[bytes, bytes] = {}
    at = 8  # past the signature
    while at < len(png):
        (length,) = struct.unpack(">I", png[at : at + 4])
        kind = png[at + 4 : at + 8]
        chunks[kind] = chunks.get(kind, b"") + png[at + 8 : at + 8 + length]
        at += length + 12
    return chunks[b"IHDR"], zlib.decompress(chunks[b"IDAT"])


@pytest.mark.parametrize("size", ICON_SIZES)
def test_bundled_icon_is_the_drawn_one(size: int) -> None:
    assert pixels(icon_path(size).read_bytes()) == pixels(render_png(size))


def test_logo_of_the_readme_is_the_drawn_one() -> None:
    assert SVG_PATH.read_text(encoding="utf-8") == render_svg()


def test_missing_icon_does_not_keep_the_app_from_speaking_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(
        messagebox, "showinfo", lambda title, message, **options: shown.append(message)
    )
    monkeypatch.setattr(
        main_window, "icon_path", lambda size: tmp_path / f"icon-{size}.png"
    )

    main_window.tell_already_running()

    assert shown == ["Uygulama zaten çalışıyor."]
    assert "Cannot show the icon" in caplog.text
