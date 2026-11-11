"""The first visible worksheet must have a header row with the columns
``Ad Soyad``, ``Gün`` and ``Saat``. Rows that cannot be used are reported, not
fatal, so a single typo does not silence the whole schedule.
"""

import datetime
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from openpyxl import load_workbook

from announcer.models import WEEKDAY_NAMES, AthleteEntry

NAME_COLUMN = "Ad Soyad"
DAY_COLUMN = "Gün"
TIME_COLUMN = "Saat"
REQUIRED_COLUMNS: tuple[str, ...] = (NAME_COLUMN, DAY_COLUMN, TIME_COLUMN)


class ScheduleFileError(Exception):
    """The schedule file as a whole cannot be used."""


class UnreadableFileError(ScheduleFileError):
    """The file is missing, inaccessible, or not a valid .xlsx workbook."""


class WrongFileTypeError(ScheduleFileError):
    """The file is not an Excel workbook, going by its name."""


class MissingColumnsError(ScheduleFileError):
    def __init__(self, columns: tuple[str, ...]) -> None:
        super().__init__(f"Missing columns: {', '.join(columns)}")
        self.columns = columns


class RowProblem(Enum):
    """The UI turns these into Turkish messages."""

    MISSING_NAME = "missing name"
    UNKNOWN_DAY = "unknown day"
    INVALID_TIME = "invalid time"


@dataclass(frozen=True)
class SkippedRow:
    row: int  # 1-based row number, as shown in Excel
    problems: tuple[RowProblem, ...]


@dataclass(frozen=True)
class LoadedSchedule:
    entries: tuple[AthleteEntry, ...]
    skipped: tuple[SkippedRow, ...]


# Hours and minutes separated by ":" or ".", optionally followed by seconds.
_TIME_PATTERN = re.compile(r"(\d{1,2})[:.](\d{2})(?:[:.][0-5]\d)?")


def _fold(text: str) -> str:
    """Accents are removed by decomposing each letter and dropping the combining
    marks, which also covers text that arrives already decomposed. The dotless
    "ı" has no decomposition, so it is replaced first. The system locale is
    deliberately not consulted.
    """
    decomposed = unicodedata.normalize("NFKD", text.replace("ı", "i"))
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).lower()


_WEEKDAYS: dict[str, int] = {
    _fold(name): index for index, name in enumerate(WEEKDAY_NAMES)
}


def load_schedule(path: Path) -> LoadedSchedule:
    """Raises ``ScheduleFileError`` if the file is not an ``.xlsx`` workbook,
    cannot be read or lacks a required column.
    """
    if path.suffix.lower() != ".xlsx":
        raise WrongFileTypeError(f"{path.name} is not an .xlsx file")
    rows = _read_rows(path)
    if not rows:
        raise MissingColumnsError(REQUIRED_COLUMNS)
    name_at, day_at, time_at = _locate_columns(rows[0])

    entries: list[AthleteEntry] = []
    skipped: list[SkippedRow] = []
    for number, row in enumerate(rows[1:], start=2):
        name = _clean(_cell(row, name_at))
        day = _clean(_cell(row, day_at))
        time_cell = _cell(row, time_at)
        if not name and not day and not _clean(time_cell):
            continue

        weekday = _WEEKDAYS.get(_fold(day))
        time = _parse_time(time_cell)
        problems: list[RowProblem] = []
        if not name:
            problems.append(RowProblem.MISSING_NAME)
        if weekday is None:
            problems.append(RowProblem.UNKNOWN_DAY)
        if time is None:
            problems.append(RowProblem.INVALID_TIME)
        # The explicit None checks let the type checker narrow both values.
        if problems or weekday is None or time is None:
            skipped.append(SkippedRow(number, tuple(problems)))
            continue
        entries.append(AthleteEntry(name, weekday, time))

    return LoadedSchedule(tuple(entries), tuple(skipped))


def _read_rows(path: Path) -> list[tuple[object, ...]]:
    try:
        # Read-only mode streams rows, so stray formatting far below the data
        # does not cost time or memory.
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = next(
                sheet for sheet in workbook.worksheets if sheet.sheet_state == "visible"
            )
            return list(sheet.iter_rows(values_only=True))
        finally:
            workbook.close()
    # openpyxl raises many unrelated exception types for missing, damaged or
    # non-workbook files, so everything is treated as an unreadable file.
    except Exception as error:
        raise UnreadableFileError(f"Cannot read {path}") from error


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


def _cell(row: tuple[object, ...], index: int) -> object:
    """Rows may be shorter than the header."""
    return row[index] if index < len(row) else None


def _clean(value: object) -> str:
    return "" if value is None else " ".join(str(value).split())


def _parse_time(value: object) -> datetime.time | None:
    """Excel hands back a ``time`` for time-formatted cells and a string for text
    cells, so both are accepted. A ``datetime`` is rejected: it means Excel
    turned the typed value into a date, so its time part is not what was meant.
    """
    if isinstance(value, datetime.time):
        return datetime.time(value.hour, value.minute)
    if isinstance(value, str):
        match = _TIME_PATTERN.fullmatch(value.strip())
        if match:
            hour, minute = int(match[1]), int(match[2])
            if hour < 24 and minute < 60:
                return datetime.time(hour, minute)
    return None
