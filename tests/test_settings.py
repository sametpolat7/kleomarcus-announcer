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
from announcer.template import DEFAULT_END_TEMPLATE, DEFAULT_TEMPLATE

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


def test_speed_and_delays_that_are_not_offered_are_rejected() -> None:
    with pytest.raises(ValueError, match="speed"):
        Settings(speed=1.5)
    with pytest.raises(ValueError, match="delay"):
        Settings(start_delay=4)
    with pytest.raises(ValueError, match="delay"):
        Settings(end_delay=4)


def test_missing_file_gives_the_defaults(path: Path) -> None:
    assert load_settings(path) == LoadedSettings(Settings(), dropped=0)


def test_round_trip(path: Path) -> None:
    settings = Settings(
        voice="tr-TR-AhmetNeural",
        schedule_path=Path("program") / "haftalık.xlsx",
        template="Sayın {ad_soyad}, dersiniz başlıyor.",
        custom_announcements=(CLOSING, CLEANING),
        speed=0.9,
        start_delay=2,
        end_delay=5,
    )

    save_settings(settings, path)

    assert load_settings(path) == LoadedSettings(settings, dropped=0)


def test_saved_file_can_be_read_and_edited_by_hand(path: Path) -> None:
    save_settings(Settings(custom_announcements=(CLEANING,)), path)

    text = path.read_text(encoding="utf-8")
    assert json.loads(text) == {
        "voice": DEFAULT_VOICE,
        "speed": 1.0,
        "start_delay_minutes": 0,
        "end_delay_minutes": 0,
        "schedule_path": None,
        "template": DEFAULT_TEMPLATE,
        "end_template": DEFAULT_END_TEMPLATE,
        "end_announcements": True,
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
        ("end_template", "Görüşmek üzere."),
        ("end_template", 3),
        ("end_announcements", "false"),
        ("end_announcements", 0),
        ("custom_announcements", {"text": "Duyuru"}),
        ("speed", 0.85),
        ("speed", "1.1"),
        ("speed", True),
        ("start_delay_minutes", 4),
        ("start_delay_minutes", -1),
        ("start_delay_minutes", "2"),
        ("start_delay_minutes", True),
        ("end_delay_minutes", 4),
        ("end_delay_minutes", None),
        ("delay_minutes", 4),
    ],
)
def test_unusable_value_falls_back_to_its_default(
    path: Path, name: str, value: object
) -> None:
    write_json(path, {name: value})

    assert load_settings(path) == LoadedSettings(Settings(), dropped=0)


def test_file_of_an_older_version_announces_the_end_with_the_default(
    path: Path,
) -> None:
    write_json(path, {"template": "Hoş geldin {ad_soyad}."})

    settings = load_settings(path).settings

    assert settings.end_template == DEFAULT_END_TEMPLATE
    assert settings.end_announcements is True


def test_end_template_and_switch_are_kept(path: Path) -> None:
    saved = Settings(end_template="Güle güle {ad_soyad}.", end_announcements=False)
    save_settings(saved, path)

    assert load_settings(path).settings == saved


def test_speed_and_delay_may_be_written_as_any_number(path: Path) -> None:
    write_json(path, {"speed": 1, "start_delay_minutes": 5.0})

    settings = load_settings(path).settings

    assert (settings.speed, settings.start_delay) == (1.0, 5)
    assert (type(settings.speed), type(settings.start_delay)) == (float, int)


def test_single_delay_of_an_older_version_is_kept_for_the_starts_and_the_ends(
    path: Path,
) -> None:
    write_json(path, {"delay_minutes": 5})

    settings = load_settings(path).settings

    assert (settings.start_delay, settings.end_delay) == (5, 5)


def test_delay_of_its_own_beats_the_single_one_of_an_older_version(path: Path) -> None:
    write_json(path, {"delay_minutes": 5, "end_delay_minutes": 0})

    settings = load_settings(path).settings

    assert (settings.start_delay, settings.end_delay) == (5, 0)


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
