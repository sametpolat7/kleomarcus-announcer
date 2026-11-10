"""Tests for loading and saving the settings file."""

import datetime
import json
from pathlib import Path

import pytest

from announcer.models import CustomAnnouncement
from announcer.settings import (
    DEFAULT_VOICE,
    LoadedSettings,
    Settings,
    SettingsFileError,
    load_settings,
    save_settings,
)
from announcer.template import DEFAULT_TEMPLATE

CLOSING = CustomAnnouncement("Salon 22:00'de kapanacaktır.", datetime.time(21, 45))
CLEANING = CustomAnnouncement(
    "Lütfen ekipmanları yerine bırakınız.", datetime.time(9, 5), frozenset({5, 0})
)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "config.json"


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_template_without_the_placeholder_is_rejected() -> None:
    with pytest.raises(ValueError, match="placeholder"):
        Settings(template="Antrenmanınız başlamıştır.")


def test_missing_file_gives_the_defaults(path: Path) -> None:
    assert load_settings(path) == LoadedSettings(Settings(), dropped=0)


def test_round_trip(path: Path) -> None:
    settings = Settings(
        voice="tr-TR-AhmetNeural",
        schedule_path=Path("program") / "haftalık.xlsx",
        template="Sayın {ad_soyad}, dersiniz başlıyor.",
        custom_announcements=(CLOSING, CLEANING),
    )

    save_settings(settings, path)

    assert load_settings(path) == LoadedSettings(settings, dropped=0)


def test_saved_file_can_be_read_and_edited_by_hand(path: Path) -> None:
    save_settings(Settings(custom_announcements=(CLEANING,)), path)

    text = path.read_text(encoding="utf-8")
    assert json.loads(text) == {
        "voice": DEFAULT_VOICE,
        "schedule_path": None,
        "template": DEFAULT_TEMPLATE,
        "custom_announcements": [
            {
                "text": "Lütfen ekipmanları yerine bırakınız.",
                "time": "09:05",
                "weekdays": [0, 5],
            }
        ],
    }
    # Turkish letters are stored as themselves, so the file stays editable.
    assert "antrenmanınız" in text
    assert text.endswith("}\n")


def test_file_edited_by_hand_loads(path: Path) -> None:
    # Notepad may add a byte order mark; values may be missing or unknown.
    path.write_text(
        '{"voice": "  tr-TR-AhmetNeural\\n", "volume": 11}', encoding="utf-8-sig"
    )

    assert load_settings(path).settings == Settings(voice="tr-TR-AhmetNeural")


def test_template_stored_with_the_old_placeholder_keeps_working(path: Path) -> None:
    write_json(path, {"template": "Sayın {ad}, hoş geldiniz."})

    assert load_settings(path).settings.template == "Sayın {ad_soyad}, hoş geldiniz."


@pytest.mark.parametrize(
    "content",
    [b"{", b"[]", b"\xff\xfe\x00", b"[" * 200_000],
    ids=["cut short", "not an object", "not utf-8", "deeply nested"],
)
def test_unreadable_file_is_an_error(path: Path, content: bytes) -> None:
    path.write_bytes(content)

    with pytest.raises(SettingsFileError):
        load_settings(path)


def test_folder_in_place_of_the_file_is_an_error(path: Path) -> None:
    path.mkdir()

    with pytest.raises(SettingsFileError):
        load_settings(path)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("voice", "   "),
        ("voice", 5),
        ("schedule_path", ""),
        ("schedule_path", {"path": "a.xlsx"}),
        ("template", "Hoş geldiniz."),
        ("template", ["{ad_soyad}"]),
        ("custom_announcements", {"text": "Duyuru"}),
    ],
)
def test_unusable_value_falls_back_to_its_default(
    path: Path, name: str, value: object
) -> None:
    write_json(path, {name: value})

    assert load_settings(path) == LoadedSettings(Settings(), dropped=0)


@pytest.mark.parametrize(
    "stored",
    [
        "Duyuru",
        {"time": "12:00", "weekdays": [0]},
        {"text": "", "time": "12:00", "weekdays": [0]},
        {"text": "Duyuru", "time": "24:00", "weekdays": [0]},
        {"text": "Duyuru", "time": 1200, "weekdays": [0]},
        {"text": "Duyuru", "time": "12:00"},
        {"text": "Duyuru", "time": "12:00", "weekdays": [7]},
        {"text": "Duyuru", "time": "12:00", "weekdays": [True]},
        {"text": "Duyuru", "time": "12:00", "weekdays": "0"},
    ],
)
def test_unusable_custom_announcement_is_left_out_and_counted(
    path: Path, stored: object
) -> None:
    kept = {"text": "Kalan duyuru", "time": "8:30", "weekdays": [6, 6, 2]}
    write_json(path, {"custom_announcements": [stored, kept]})

    assert load_settings(path) == LoadedSettings(
        Settings(
            custom_announcements=(
                CustomAnnouncement(
                    "Kalan duyuru", datetime.time(8, 30), frozenset({2, 6})
                ),
            )
        ),
        dropped=1,
    )
