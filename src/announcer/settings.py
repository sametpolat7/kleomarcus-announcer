"""Load and save the app's settings as a single JSON file.

The file holds everything the app remembers between runs: the voice, the last
schedule file, the announcement template and the custom announcements.
"""

import datetime
import json
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir

from announcer import APP_NAME
from announcer.files import write_atomically
from announcer.models import CustomAnnouncement
from announcer.template import DEFAULT_TEMPLATE, PLACEHOLDER, is_valid_template

DEFAULT_VOICE = "tr-TR-EmelNeural"

_TIME_FORMAT = "%H:%M"
_OLD_PLACEHOLDER = "{ad}"  # what the placeholder was called at first


class SettingsFileError(Exception):
    """The settings file exists but cannot be read as settings."""


@dataclass(frozen=True)
class Settings:
    """What the app remembers between runs."""

    voice: str = DEFAULT_VOICE
    schedule_path: Path | None = None  # the Excel file that was loaded last
    template: str = DEFAULT_TEMPLATE
    custom_announcements: tuple[CustomAnnouncement, ...] = ()

    def __post_init__(self) -> None:
        if not is_valid_template(self.template):
            raise ValueError(f"The template has no {PLACEHOLDER} placeholder")


@dataclass(frozen=True)
class LoadedSettings:
    """The settings read from a file, plus what had to be left out of them."""

    settings: Settings
    dropped: int = 0  # how many custom announcements were unusable


def settings_path() -> Path:
    """Return where the settings file lives for the current user."""
    return Path(user_config_dir(APP_NAME, appauthor=False)) / "config.json"


def load_settings(path: Path) -> LoadedSettings:
    """Load the settings at ``path``, or the defaults if there is no such file.

    The file may have been edited by hand, so a value that is missing or
    unusable falls back to its default, and a custom announcement that is
    unusable is left out and counted. Raises ``SettingsFileError`` if the file
    as a whole cannot be read.
    """
    try:
        # "utf-8-sig" also accepts the byte order mark that Notepad may add.
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return LoadedSettings(Settings())
    # A file nested deeply enough makes the parser raise ``RecursionError``.
    except (OSError, ValueError, RecursionError) as error:
        raise SettingsFileError(f"Cannot read {path}") from error
    if not isinstance(data, dict):
        raise SettingsFileError(f"{path} does not hold a JSON object")

    voice = _text(data.get("voice"))
    schedule_path = _text(data.get("schedule_path"))
    template = data.get("template")
    if isinstance(template, str) and PLACEHOLDER not in template:
        # A template that was stored under the old name keeps working.
        template = template.replace(_OLD_PLACEHOLDER, PLACEHOLDER)
    stored = data.get("custom_announcements")
    found = (
        [_custom_announcement(item) for item in stored]
        if isinstance(stored, list)
        else []
    )
    usable = tuple(item for item in found if item is not None)
    settings = Settings(
        voice=voice or DEFAULT_VOICE,
        schedule_path=Path(schedule_path) if schedule_path else None,
        template=(
            template
            if isinstance(template, str) and is_valid_template(template)
            else DEFAULT_TEMPLATE
        ),
        custom_announcements=usable,
    )
    return LoadedSettings(settings, dropped=len(found) - len(usable))


def save_settings(settings: Settings, path: Path) -> None:
    """Write ``settings`` to ``path``, creating its folder if needed.

    A save that is cut short leaves the previous settings in place. Raises
    ``OSError`` if the file cannot be written.
    """
    data = {
        "voice": settings.voice,
        "schedule_path": (
            None if settings.schedule_path is None else str(settings.schedule_path)
        ),
        "template": settings.template,
        "custom_announcements": [
            {
                "text": announcement.text,
                "time": announcement.time.strftime(_TIME_FORMAT),
                "weekdays": sorted(announcement.weekdays),
            }
            for announcement in settings.custom_announcements
        ],
    }
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    write_atomically(path, text.encode("utf-8"))


def _text(value: object) -> str | None:
    """Return ``value`` trimmed if it is a string with something in it."""
    return value.strip() or None if isinstance(value, str) else None


def _custom_announcement(value: object) -> CustomAnnouncement | None:
    """Return one stored custom announcement, or None if it is unusable."""
    if not isinstance(value, dict):
        return None
    text, time, weekdays = value.get("text"), value.get("time"), value.get("weekdays")
    if not (
        isinstance(text, str) and isinstance(time, str) and isinstance(weekdays, list)
    ):
        return None
    # ``True`` and ``False`` are instances of ``int``, so the exact type is checked.
    if any(type(day) is not int for day in weekdays):
        return None
    try:
        return CustomAnnouncement(
            text,
            datetime.datetime.strptime(time, _TIME_FORMAT).time(),
            frozenset(weekdays),
        )
    except ValueError:
        return None
