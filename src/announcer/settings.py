import datetime
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir

from announcer import APP_NAME
from announcer.files import write_atomically
from announcer.models import DELAYS, NORMAL_SPEED, SPEEDS, CustomAnnouncement
from announcer.template import (
    DEFAULT_END_TEMPLATE,
    DEFAULT_TEMPLATE,
    PLACEHOLDER,
    is_valid_template,
)

DEFAULT_VOICE = "tr-TR-EmelNeural"

_TIME_FORMAT = "%H:%M"


class SettingsFileError(Exception):
    """The settings file exists but cannot be read as settings."""


@dataclass(frozen=True)
class Settings:
    voice: str = DEFAULT_VOICE
    schedule_path: Path | None = None
    template: str = DEFAULT_TEMPLATE  # for the start of a session
    end_template: str = DEFAULT_END_TEMPLATE
    end_announcements: bool = True
    custom_announcements: tuple[CustomAnnouncement, ...] = ()
    speed: float = NORMAL_SPEED
    # Minutes that the start and the end of a session are announced after
    # their time.
    start_delay: int = 0
    end_delay: int = 0

    def __post_init__(self) -> None:
        if not (
            is_valid_template(self.template) and is_valid_template(self.end_template)
        ):
            raise ValueError(f"A template has no {PLACEHOLDER} placeholder")
        if self.speed not in SPEEDS:
            raise ValueError(f"The speed must be one of {SPEEDS}")
        if self.start_delay not in DELAYS or self.end_delay not in DELAYS:
            raise ValueError(f"A delay must be one of {DELAYS}")


@dataclass(frozen=True)
class LoadedSettings:
    settings: Settings
    dropped: int = 0  # how many custom announcements were unusable


def settings_path() -> Path:
    return Path(user_config_dir(APP_NAME, appauthor=False)) / "config.json"


def load_settings(path: Path) -> LoadedSettings:
    """The file may have been edited by hand, so a value that is missing or
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
    end_template = data.get("end_template")
    end_announcements = data.get("end_announcements")
    stored = data.get("custom_announcements")
    found = (
        [_custom_announcement(item) for item in stored]
        if isinstance(stored, list)
        else []
    )
    usable = tuple(item for item in found if item is not None)
    single_delay = _one_of(DELAYS, data.get("delay_minutes"), 0)
    settings = Settings(
        voice=voice or DEFAULT_VOICE,
        schedule_path=Path(schedule_path) if schedule_path else None,
        template=(
            template
            if isinstance(template, str) and is_valid_template(template)
            else DEFAULT_TEMPLATE
        ),
        end_template=(
            end_template
            if isinstance(end_template, str) and is_valid_template(end_template)
            else DEFAULT_END_TEMPLATE
        ),
        end_announcements=(
            end_announcements if isinstance(end_announcements, bool) else True
        ),
        custom_announcements=usable,
        speed=_one_of(SPEEDS, data.get("speed"), NORMAL_SPEED),
        start_delay=_one_of(DELAYS, data.get("start_delay_minutes"), single_delay),
        end_delay=_one_of(DELAYS, data.get("end_delay_minutes"), single_delay),
    )
    return LoadedSettings(settings, dropped=len(found) - len(usable))


def save_settings(settings: Settings, path: Path) -> None:
    """Raises ``OSError`` if the file cannot be written."""
    data = {
        "voice": settings.voice,
        "speed": settings.speed,
        "start_delay_minutes": settings.start_delay,
        "end_delay_minutes": settings.end_delay,
        "schedule_path": (
            None if settings.schedule_path is None else str(settings.schedule_path)
        ),
        "template": settings.template,
        "end_template": settings.end_template,
        "end_announcements": settings.end_announcements,
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
    return value.strip() or None if isinstance(value, str) else None


def _one_of[Choice: (int, float)](
    choices: Sequence[Choice], value: object, default: Choice
) -> Choice:
    # ``True`` equals 1, so a truth value is ruled out by its type.
    if type(value) not in (int, float):
        return default
    return next((choice for choice in choices if choice == value), default)


def _custom_announcement(value: object) -> CustomAnnouncement | None:
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
