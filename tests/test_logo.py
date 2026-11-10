"""Tests for the drawn logo, the bundled copies of it and the window's icon."""

from pathlib import Path
from tkinter import messagebox

import pytest

from announcer.logo import ICON_SIZES, icon_path, render_png, render_svg
from announcer.ui import main_window

SVG_PATH = Path(__file__).parent.parent / "docs" / "logo.svg"


@pytest.mark.parametrize("size", ICON_SIZES)
def test_bundled_icon_is_the_drawn_one(size: int) -> None:
    assert icon_path(size).read_bytes() == render_png(size)


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
