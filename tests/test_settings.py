"""Tests for loading and saving the settings file."""

import datetime
import json
from pathlib import Path

import pytest

from announcer import APP_NAME
from announcer.models import ALL_WEEKDAYS, CustomAnnouncement
from announcer.settings import (
    DEFAULT_VOICE,
    LoadedSettings,
    Settings,
    SettingsFileError,
    load_settings,
    save_settings,
    settings_path,
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


def test_settings_path_is_a_file_in_the_app_folder() -> None:
    location = settings_path()

    assert location.name == "config.json"
    assert location.parent.name == APP_NAME
    assert location.is_absolute()


def test_defaults() -> None:
    assert Settings() == Settings(
        voice="tr-TR-EmelNeural",
        schedule_path=None,
        template="{ad}, antrenmanınız başlamıştır. İyi antrenmanlar dileriz.",
        custom_announcements=(),
    )


def test_template_without_the_placeholder_is_rejected() -> None:
    with pytest.raises(ValueError, match="placeholder"):
        Settings(template="Antrenmanınız başlamıştır.")


def test_missing_file_gives_the_defaults(path: Path) -> None:
    assert load_settings(path).settings == Settings()


def test_round_trip(path: Path) -> None:
    settings = Settings(
        voice="tr-TR-AhmetNeural",
        schedule_path=Path("program") / "haftalık.xlsx",
        template="Sayın {ad}, dersiniz başlıyor.",
        custom_announcements=(CLOSING, CLEANING),
    )

    save_settings(settings, path)

    assert load_settings(path).settings == settings


def test_round_trip_of_the_defaults(path: Path) -> None:
    save_settings(Settings(), path)

    assert load_settings(path).settings == Settings()


def test_saved_file_is_readable_json(path: Path) -> None:
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


def test_save_creates_the_folder(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "folder" / "config.json"

    save_settings(Settings(), path)

    assert load_settings(path).settings == Settings()


def test_save_replaces_the_file_and_leaves_nothing_else(path: Path) -> None:
    save_settings(Settings(voice="first"), path)
    save_settings(Settings(voice="second"), path)

    assert load_settings(path).settings.voice == "second"
    assert [item.name for item in path.parent.iterdir()] == ["config.json"]


def test_file_with_a_byte_order_mark_loads(path: Path) -> None:
    path.write_text('{"voice": "tr-TR-AhmetNeural"}', encoding="utf-8-sig")

    assert load_settings(path).settings.voice == "tr-TR-AhmetNeural"


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"{",
        b'{"voice": }',
        b"[]",
        b'"text"',
        b"null",
        b"\xff\xfe\x00",
        b"[" * 200_000,
    ],
    ids=[
        "empty",
        "cut short",
        "missing value",
        "list",
        "text",
        "null",
        "not utf-8",
        "deeply nested",
    ],
)
def test_unreadable_file_is_an_error(path: Path, content: bytes) -> None:
    path.write_bytes(content)

    with pytest.raises(SettingsFileError):
        load_settings(path)


def test_folder_in_place_of_the_file_is_an_error(path: Path) -> None:
    path.mkdir()

    with pytest.raises(SettingsFileError):
        load_settings(path)


def test_missing_values_fall_back_to_the_defaults(path: Path) -> None:
    write_json(path, {"voice": "tr-TR-AhmetNeural"})

    assert load_settings(path).settings == Settings(voice="tr-TR-AhmetNeural")


def test_unknown_values_are_ignored(path: Path) -> None:
    write_json(path, {"volume": 11})

    assert load_settings(path).settings == Settings()


@pytest.mark.parametrize("voice", [None, "", "   ", 5, ["tr-TR-AhmetNeural"]])
def test_unusable_voice_falls_back_to_the_default(path: Path, voice: object) -> None:
    write_json(path, {"voice": voice})

    assert load_settings(path).settings.voice == DEFAULT_VOICE


def test_voice_is_trimmed(path: Path) -> None:
    write_json(path, {"voice": "  tr-TR-AhmetNeural\n"})

    assert load_settings(path).settings.voice == "tr-TR-AhmetNeural"


@pytest.mark.parametrize("schedule_path", [None, "", "  ", 5, {"path": "a.xlsx"}])
def test_unusable_schedule_path_is_dropped(path: Path, schedule_path: object) -> None:
    write_json(path, {"schedule_path": schedule_path})

    assert load_settings(path).settings.schedule_path is None


@pytest.mark.parametrize("template", [None, "", "Hoş geldiniz.", 5, ["{ad}"]])
def test_unusable_template_falls_back_to_the_default(
    path: Path, template: object
) -> None:
    write_json(path, {"template": template})

    assert load_settings(path).settings.template == DEFAULT_TEMPLATE


@pytest.mark.parametrize("stored", [None, "none", 5, {"text": "Duyuru"}])
def test_custom_announcements_that_are_not_a_list_are_dropped(
    path: Path, stored: object
) -> None:
    write_json(path, {"custom_announcements": stored})

    assert load_settings(path).settings.custom_announcements == ()


@pytest.mark.parametrize(
    "stored",
    [
        "Duyuru",
        {},
        {"time": "12:00", "weekdays": [0]},
        {"text": "", "time": "12:00", "weekdays": [0]},
        {"text": 5, "time": "12:00", "weekdays": [0]},
        {"text": "Duyuru", "weekdays": [0]},
        {"text": "Duyuru", "time": "24:00", "weekdays": [0]},
        {"text": "Duyuru", "time": "12:60", "weekdays": [0]},
        {"text": "Duyuru", "time": "12:00:30", "weekdays": [0]},
        {"text": "Duyuru", "time": "öğlen", "weekdays": [0]},
        {"text": "Duyuru", "time": 1200, "weekdays": [0]},
        {"text": "Duyuru", "time": "12:00"},
        {"text": "Duyuru", "time": "12:00", "weekdays": []},
        {"text": "Duyuru", "time": "12:00", "weekdays": [7]},
        {"text": "Duyuru", "time": "12:00", "weekdays": ["0"]},
        {"text": "Duyuru", "time": "12:00", "weekdays": [True]},
        {"text": "Duyuru", "time": "12:00", "weekdays": [1.0]},
        {"text": "Duyuru", "time": "12:00", "weekdays": [[0]]},
        {"text": "Duyuru", "time": "12:00", "weekdays": "0"},
    ],
)
def test_unusable_custom_announcement_is_left_out(path: Path, stored: object) -> None:
    kept = {"text": "Kalan duyuru", "time": "8:30", "weekdays": [6, 6, 2]}
    write_json(path, {"custom_announcements": [stored, kept]})

    assert load_settings(path).settings.custom_announcements == (
        CustomAnnouncement("Kalan duyuru", datetime.time(8, 30), frozenset({2, 6})),
    )


def test_every_day_is_stored_as_all_seven_weekdays(path: Path) -> None:
    save_settings(Settings(custom_announcements=(CLOSING,)), path)

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["custom_announcements"][0]["weekdays"] == [0, 1, 2, 3, 4, 5, 6]
    assert load_settings(path).settings.custom_announcements[0].weekdays == ALL_WEEKDAYS


def test_unusable_custom_announcements_are_counted(path: Path) -> None:
    kept = {"text": "Kalan duyuru", "time": "08:30", "weekdays": [2]}
    unusable = {"text": "Duyuru", "time": "18.30", "weekdays": [7]}
    write_json(path, {"custom_announcements": [unusable, kept, "Duyuru"]})

    loaded = load_settings(path)

    assert loaded.dropped == 2
    assert len(loaded.settings.custom_announcements) == 1


def test_nothing_is_counted_as_dropped_when_all_is_usable(path: Path) -> None:
    save_settings(Settings(custom_announcements=(CLOSING, CLEANING)), path)

    assert load_settings(path) == LoadedSettings(
        Settings(custom_announcements=(CLOSING, CLEANING)), dropped=0
    )
    assert load_settings(path.with_name("missing.json")).dropped == 0
