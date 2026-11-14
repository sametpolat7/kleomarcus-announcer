"""The operator's changes of a day are kept in a file of their own, so that a
restart keeps them.
"""

import datetime
import json
from collections.abc import Iterable
from pathlib import Path

from announcer.files import write_atomically
from announcer.models import Change, Kind

_DAY_FORMAT = "%Y-%m-%d"
_MOMENT_FORMAT = "%Y-%m-%d %H:%M"
_TIME_FORMAT = "%H:%M"


class ChangesFileError(Exception):
    """The file exists but cannot be read as changes."""


def load_changes(path: Path, today: datetime.date) -> tuple[Change, ...]:
    """A change that is unusable is left out. Raises ``ChangesFileError`` if the
    file as a whole cannot be read."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return ()
    except (OSError, ValueError, RecursionError) as error:
        raise ChangesFileError(f"Cannot read {path}") from error
    stored = data.get("changes") if isinstance(data, dict) else None
    if not isinstance(stored, list):
        raise ChangesFileError(f"{path} holds no list of changes")
    found = (_change(item) for item in stored)
    return tuple(
        change for change in found if change is not None and change.day >= today
    )


def save_changes(changes: Iterable[Change], path: Path) -> None:
    """Raises ``OSError`` if the file cannot be written."""
    data = {
        "changes": [
            {
                "day": change.day.strftime(_DAY_FORMAT),
                "set_for": change.set_for.strftime(_MOMENT_FORMAT),
                "kind": change.kind.value,
                "name": change.name,
                "skipped": change.skipped,
                "time": None
                if change.time is None
                else change.time.strftime(_TIME_FORMAT),
                "text": change.text,
            }
            for change in changes
        ]
    }
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    write_atomically(path, text.encode("utf-8"))


def _change(value: object) -> Change | None:
    if not isinstance(value, dict):
        return None
    try:
        day = datetime.datetime.strptime(value["day"], _DAY_FORMAT).date()
        set_for = datetime.datetime.strptime(value["set_for"], _MOMENT_FORMAT)
        kind = Kind(value["kind"])
        time = value.get("time")
        name, text = value.get("name", ""), value.get("text")
        skipped = value.get("skipped", False)
        if not (
            isinstance(skipped, bool)
            and isinstance(name, str)
            and (text is None or isinstance(text, str))
            and kind in (Kind.START, Kind.END, Kind.CUSTOM)
            and bool(name) == (kind is not Kind.CUSTOM)
        ):
            return None
        return Change(
            day,
            set_for,
            kind,
            name,
            skipped,
            None
            if time is None
            else datetime.datetime.strptime(time, _TIME_FORMAT).time(),
            text,
        )
    except KeyError, TypeError, ValueError:
        return None
