"""Read and validate the athlete schedule from an Excel file.

The first worksheet must have a header row with the columns ``Ad Soyad``,
``Gün`` and ``Saat``. Rows that cannot be used are reported, not fatal, so a
single typo does not silence the whole schedule.
"""

import datetime
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from announcer.models import WEEKDAY_NAMES, AthleteEntry

NAME_COLUMN = "Ad Soyad"
DAY_COLUMN = "Gün"
TIME_COLUMN = "Saat"
REQUIRED_COLUMNS: tuple[str, ...] = (NAME_COLUMN, DAY_COLUMN, TIME_COLUMN)


class ScheduleFileError(Exception):
    """The schedule file as a whole cannot be used."""


class UnreadableFileError(ScheduleFileError):
    """The file is missing, inaccessible, or not a valid .xlsx workbook."""


class MissingColumnsError(ScheduleFileError):
    """The header row lacks one or more required columns."""

    def __init__(self, columns: tuple[str, ...]) -> None:
        super().__init__(f"Missing columns: {', '.join(columns)}")
        self.columns = columns


class RowProblem(Enum):
    """Why a row was skipped. The UI turns these into Turkish messages."""

    MISSING_NAME = "missing name"
    UNKNOWN_DAY = "unknown day"
    INVALID_TIME = "invalid time"


@dataclass(frozen=True)
class SkippedRow:
    """A row that was left out of the schedule."""

    row: int  # 1-based row number, as shown in Excel
    problems: tuple[RowProblem, ...]


@dataclass(frozen=True)
class LoadedSchedule:
    """The usable entries of a schedule file, plus the rows that were skipped."""

    entries: tuple[AthleteEntry, ...]
    skipped: tuple[SkippedRow, ...]


# Turkish letters are folded to ASCII before matching, so that "SALI", "Salı"
# and "sali" all name the same day. ``str.lower()`` alone gets "İ" and "I"
# wrong for Turkish, and the system locale is deliberately not consulted.
_FOLD = str.maketrans("İIıŞşÇçĞğÜüÖö", "iiissccgguuoo")

# Hours and minutes separated by ":" or ".", optionally followed by seconds.
_TIME_PATTERN = re.compile(r"(\d{1,2})[:.](\d{2})(?:[:.]\d{2})?")


def _fold(text: str) -> str:
    """Normalize text for matching: ignore case, Turkish accents and spacing."""
    return " ".join(text.translate(_FOLD).lower().split())


_WEEKDAYS: dict[str, int] = {
    _fold(name): index for index, name in enumerate(WEEKDAY_NAMES)
}


def load_schedule(path: Path) -> LoadedSchedule:
    """Load the schedule at ``path``.

    Raises ``ScheduleFileError`` if the file cannot be read or lacks a
    required column.
    """
    rows = _read_rows(path)
    if not rows:
        raise MissingColumnsError(REQUIRED_COLUMNS)
    name_at, day_at, time_at = _locate_columns(rows[0])

    entries: list[AthleteEntry] = []
    skipped: list[SkippedRow] = []
    for number, row in enumerate(rows[1:], start=2):
        name = _clean(row[name_at])
        day = _clean(row[day_at])
        if not name and not day and not _clean(row[time_at]):
            continue  # blank row

        weekday = _WEEKDAYS.get(_fold(day))
        time = _parse_time(row[time_at])
        problems: list[RowProblem] = []
        if not name:
            problems.append(RowProblem.MISSING_NAME)
        if weekday is None:
            problems.append(RowProblem.UNKNOWN_DAY)
        if time is None:
            problems.append(RowProblem.INVALID_TIME)
        if weekday is None or time is None or problems:
            skipped.append(SkippedRow(number, tuple(problems)))
            continue
        entries.append(AthleteEntry(name, weekday, time))

    return LoadedSchedule(tuple(entries), tuple(skipped))


def _read_rows(path: Path) -> list[tuple[object, ...]]:
    """Return every row of the first worksheet as a tuple of cell values."""
    try:
        workbook = load_workbook(path, data_only=True)
    # openpyxl raises KeyError for a zip archive that is not a workbook.
    except (OSError, BadZipFile, InvalidFileException, KeyError) as error:
        raise UnreadableFileError(f"Cannot read {path}") from error
    return list(workbook.worksheets[0].iter_rows(values_only=True))


def _locate_columns(header: tuple[object, ...]) -> tuple[int, int, int]:
    """Return the indexes of the name, day and time columns."""
    positions: dict[str, int] = {}
    for index, cell in enumerate(header):
        positions.setdefault(_fold(_clean(cell)), index)

    missing = tuple(
        column for column in REQUIRED_COLUMNS if _fold(column) not in positions
    )
    if missing:
        raise MissingColumnsError(missing)
    return (
        positions[_fold(NAME_COLUMN)],
        positions[_fold(DAY_COLUMN)],
        positions[_fold(TIME_COLUMN)],
    )


def _clean(value: object) -> str:
    """Return a cell value as text with surrounding and repeated spaces removed."""
    return "" if value is None else " ".join(str(value).split())


def _parse_time(value: object) -> datetime.time | None:
    """Return a cell value as a time of day to the minute, or None if invalid.

    Excel hands back a ``time`` for time-formatted cells and a string for text
    cells, so both are accepted.
    """
    if isinstance(value, datetime.datetime):
        value = value.time()
    if isinstance(value, datetime.time):
        return datetime.time(value.hour, value.minute)
    if isinstance(value, str):
        match = _TIME_PATTERN.fullmatch(value.strip())
        if match:
            hour, minute = int(match[1]), int(match[2])
            if hour < 24 and minute < 60:
                return datetime.time(hour, minute)
    return None
