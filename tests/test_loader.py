"""Tests for reading and validating the Excel schedule."""

import datetime
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from openpyxl import Workbook

from announcer.loader import (
    LoadedSchedule,
    MissingColumnsError,
    RowProblem,
    SkippedRow,
    UnreadableFileError,
    load_schedule,
)
from announcer.models import AthleteEntry

HEADER = ("Ad Soyad", "Gün", "Saat")
SAMPLE_SCHEDULE = Path(__file__).resolve().parent.parent / "sample_schedule.xlsx"

type Row = Sequence[object]
type LoadRows = Callable[..., LoadedSchedule]


@pytest.fixture
def load_rows(tmp_path: Path) -> LoadRows:
    """Write the given rows to a workbook and load it as a schedule."""

    def _load_rows(*rows: Row, header: Row = HEADER) -> LoadedSchedule:
        workbook = Workbook()
        sheet = workbook.worksheets[0]
        sheet.append(list(header))
        for row in rows:
            sheet.append(list(row))
        path = tmp_path / "schedule.xlsx"
        workbook.save(path)
        return load_schedule(path)

    return _load_rows


def test_loads_entries_in_file_order(load_rows: LoadRows) -> None:
    schedule = load_rows(
        ("Ayşe Yılmaz", "Pazartesi", "18:00"),
        ("Mehmet Demir", "Pazar", "09:30"),
        ("Zeynep Kaya", "Pazartesi", "18:00"),
    )

    assert schedule.entries == (
        AthleteEntry("Ayşe Yılmaz", 0, datetime.time(18, 0)),
        AthleteEntry("Mehmet Demir", 6, datetime.time(9, 30)),
        AthleteEntry("Zeynep Kaya", 0, datetime.time(18, 0)),
    )
    assert schedule.skipped == ()


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        (datetime.time(18, 30), datetime.time(18, 30)),
        (datetime.time(18, 30, 45), datetime.time(18, 30)),
        (datetime.datetime(2026, 1, 5, 7, 5), datetime.time(7, 5)),
        ("18:30", datetime.time(18, 30)),
        ("9:05", datetime.time(9, 5)),
        ("18.30", datetime.time(18, 30)),
        ("18:30:00", datetime.time(18, 30)),
        ("  18:30  ", datetime.time(18, 30)),
        ("00:00", datetime.time(0, 0)),
        ("23:59", datetime.time(23, 59)),
    ],
)
def test_normalizes_time_values(
    load_rows: LoadRows, cell: object, expected: datetime.time
) -> None:
    schedule = load_rows(("Ayşe Yılmaz", "Salı", cell))

    assert [entry.time for entry in schedule.entries] == [expected]


@pytest.mark.parametrize("cell", ["24:00", "18:60", "1830", "akşam", 18.5, 18, None])
def test_skips_rows_with_an_invalid_time(load_rows: LoadRows, cell: object) -> None:
    schedule = load_rows(("Ayşe Yılmaz", "Salı", cell))

    assert schedule.entries == ()
    assert schedule.skipped == (SkippedRow(2, (RowProblem.INVALID_TIME,)),)


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("Pazartesi", 0),
        ("Salı", 1),
        ("Çarşamba", 2),
        ("Perşembe", 3),
        ("Cuma", 4),
        ("Cumartesi", 5),
        ("Pazar", 6),
        ("PAZARTESİ", 0),
        ("PAZARTESI", 0),
        ("SALI", 1),
        ("sali", 1),
        ("carsamba", 2),
        ("  perşembe ", 3),
    ],
)
def test_maps_turkish_weekday_names(
    load_rows: LoadRows, cell: str, expected: int
) -> None:
    schedule = load_rows(("Ayşe Yılmaz", cell, "18:00"))

    assert [entry.weekday for entry in schedule.entries] == [expected]


@pytest.mark.parametrize("cell", ["Monday", "Pzt", "Pazartesi-Salı", 1, None])
def test_skips_rows_with_an_unknown_day(load_rows: LoadRows, cell: object) -> None:
    schedule = load_rows(("Ayşe Yılmaz", cell, "18:00"))

    assert schedule.entries == ()
    assert schedule.skipped == (SkippedRow(2, (RowProblem.UNKNOWN_DAY,)),)


def test_reports_every_problem_with_its_excel_row_number(load_rows: LoadRows) -> None:
    schedule = load_rows(
        ("Ayşe Yılmaz", "Pazartesi", "18:00"),
        (None, "Salı", "18:00"),
        ("Mehmet Demir", "Cuma", "19:00"),
        ("   ", "Funday", "soon"),
    )

    assert [entry.name for entry in schedule.entries] == ["Ayşe Yılmaz", "Mehmet Demir"]
    assert schedule.skipped == (
        SkippedRow(3, (RowProblem.MISSING_NAME,)),
        SkippedRow(
            5,
            (
                RowProblem.MISSING_NAME,
                RowProblem.UNKNOWN_DAY,
                RowProblem.INVALID_TIME,
            ),
        ),
    )


def test_ignores_blank_rows(load_rows: LoadRows) -> None:
    schedule = load_rows(
        (None, None, None),
        ("Ayşe Yılmaz", "Pazartesi", "18:00"),
        ("  ", "", None),
    )

    assert [entry.name for entry in schedule.entries] == ["Ayşe Yılmaz"]
    assert schedule.skipped == ()


def test_collapses_extra_spaces_in_names(load_rows: LoadRows) -> None:
    schedule = load_rows(("  Ayşe   Yılmaz ", "Pazartesi", "18:00"))

    assert [entry.name for entry in schedule.entries] == ["Ayşe Yılmaz"]


def test_finds_columns_by_header_regardless_of_order_and_case(
    load_rows: LoadRows,
) -> None:
    schedule = load_rows(
        ("note", "18:00", "Cuma", "Ayşe Yılmaz"),
        header=("Not", " SAAT ", "gün", "AD  SOYAD"),
    )

    assert schedule.entries == (AthleteEntry("Ayşe Yılmaz", 4, datetime.time(18, 0)),)


def test_rejects_a_file_with_missing_columns(load_rows: LoadRows) -> None:
    with pytest.raises(MissingColumnsError) as error:
        load_rows(("Ayşe Yılmaz", "18:00"), header=("Ad Soyad", "Saat", "Cinsiyet"))

    assert error.value.columns == ("Gün",)


def test_rejects_an_empty_workbook(tmp_path: Path) -> None:
    path = tmp_path / "empty.xlsx"
    Workbook().save(path)

    with pytest.raises(MissingColumnsError) as error:
        load_schedule(path)

    assert error.value.columns == ("Ad Soyad", "Gün", "Saat")


def test_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(UnreadableFileError):
        load_schedule(tmp_path / "missing.xlsx")


def test_rejects_a_file_that_is_not_a_workbook(tmp_path: Path) -> None:
    path = tmp_path / "broken.xlsx"
    path.write_text("not a spreadsheet", encoding="utf-8")

    with pytest.raises(UnreadableFileError):
        load_schedule(path)


def test_sample_schedule_loads_without_skipped_rows() -> None:
    schedule = load_schedule(SAMPLE_SCHEDULE)

    assert len(schedule.entries) == 12
    assert schedule.skipped == ()
