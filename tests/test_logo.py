import struct
import sys
import zlib
from pathlib import Path
from tkinter import messagebox

import pytest

from announcer.logo import (
    ICO_SIZES,
    ICON_SIZES,
    icon_path,
    render_ico,
    render_png,
    render_svg,
)
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


def test_margin_shrinks_the_logo_and_leaves_the_rest_empty() -> None:
    _, data = pixels(render_png(64, margin=0.25))
    _, half = pixels(render_png(32))
    # Every row begins with one byte for its filter; a pixel takes four.
    rows = [data[row * 257 + 1 : (row + 1) * 257] for row in range(64)]

    middle = [row[16 * 4 : 48 * 4] for row in rows[16:48]]
    around = [*rows[:16], *rows[48:]] + [
        row[: 16 * 4] + row[48 * 4 :] for row in rows[16:48]
    ]

    assert b"".join(b"\x00" + row for row in middle) == half
    assert not any(b"".join(around)[3::4])  # the fourth byte says how solid


def test_windows_icon_holds_the_logo_in_every_size() -> None:
    ico = render_ico()

    assert struct.unpack("<HHH", ico[:6]) == (0, 1, len(ICO_SIZES))
    for index, size in enumerate(ICO_SIZES):
        entry = ico[6 + 16 * index : 22 + 16 * index]
        width, height, _, _, _, bits, length, at = struct.unpack("<BBBBHHII", entry)
        assert (width, height, bits) == (size % 256, size % 256, 32)
        assert pixels(ico[at : at + length]) == pixels(render_png(size))


def test_packaged_mac_app_keeps_the_icon_of_its_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(messagebox, "showinfo", lambda title, message, **options: None)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    # Reading this icon would fail, so no error means that it was not read.
    monkeypatch.setattr(
        main_window, "icon_path", lambda size: tmp_path / f"icon-{size}.png"
    )

    main_window.tell_already_running()

    assert "Cannot show the icon" not in caplog.text


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
