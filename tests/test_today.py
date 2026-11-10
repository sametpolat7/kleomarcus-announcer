"""Tests for today's preview and for judging how an announcement fared."""

import datetime

import pytest

from announcer.models import AthleteEntry, CustomAnnouncement
from announcer.player import Playback
from announcer.timetable import Slot, Timetable, build_timetable
from announcer.today import Row, Status, outcome_status, summary, todays_rows

MONDAY = datetime.date(2026, 11, 9)
NINE, SIX_PM, SEVEN_PM = (datetime.time(hour) for hour in (9, 18, 19))

AYSE = AthleteEntry("Ayşe Yılmaz", 0, SIX_PM)
MEHMET = AthleteEntry("Mehmet Demir", 0, SIX_PM)
ZEYNEP = AthleteEntry("Zeynep Kaya", 1, SIX_PM)
WATER = CustomAnnouncement("Su içmeyi unutmayın.", SEVEN_PM)
TIMETABLE = build_timetable([AYSE, MEHMET, ZEYNEP], [WATER], "{ad}, hoş geldiniz.")


def at(hour: int, minute: int = 0, second: int = 0) -> datetime.datetime:
    """Return a moment on the test Monday."""
    return datetime.datetime.combine(MONDAY, datetime.time(hour, minute, second))


ATHLETES, REMINDER = "Ayşe Yılmaz, Mehmet Demir", "Su içmeyi unutmayın."


def statuses(
    now: datetime.datetime,
    outcomes: dict[tuple[datetime.datetime, str], Status] | None = None,
) -> list[Status]:
    return [row.status for row in todays_rows(TIMETABLE, now, outcomes or {})]


def test_summary_is_the_names_of_athletes_or_the_custom_text() -> None:
    athletes, announcement = TIMETABLE.slots_on(0)

    assert summary(athletes) == "Ayşe Yılmaz, Mehmet Demir"
    assert summary(announcement) == "Su içmeyi unutmayın."


def test_rows_list_todays_slots_in_time_order() -> None:
    assert todays_rows(TIMETABLE, at(8), {}) == (
        Row(SIX_PM, False, "Ayşe Yılmaz, Mehmet Demir", Status.NEXT),
        Row(SEVEN_PM, True, "Su içmeyi unutmayın.", Status.WAITING),
    )


def test_rows_are_those_of_the_weekday_of_now() -> None:
    tuesday = at(8) + datetime.timedelta(days=1)
    sunday = at(8) + datetime.timedelta(days=6)

    assert [row.summary for row in todays_rows(TIMETABLE, tuesday, {})] == [
        "Zeynep Kaya",
        "Su içmeyi unutmayın.",
    ]
    assert [row.summary for row in todays_rows(TIMETABLE, sunday, {})] == [
        "Su içmeyi unutmayın."
    ]
    assert todays_rows(Timetable(), at(8), {}) == ()


def test_only_the_first_slot_to_come_is_next() -> None:
    assert statuses(at(17, 59, 59)) == [Status.NEXT, Status.WAITING]
    assert statuses(at(18, 1)) == [Status.PASSED, Status.NEXT]
    assert statuses(at(23)) == [Status.PASSED, Status.PASSED]


def test_slot_is_not_passed_while_its_minute_runs() -> None:
    assert statuses(at(18, 0, 59)) == [Status.NEXT, Status.WAITING]


def test_known_outcome_is_shown_instead_of_the_clock() -> None:
    outcomes = {
        (at(18), ATHLETES): Status.PLAYED,
        (at(19), REMINDER): Status.PLAYING,
    }

    assert statuses(at(19, 0, 2), outcomes) == [Status.PLAYED, Status.PLAYING]
    assert statuses(at(17), {(at(18), ATHLETES): Status.ASLEEP}) == [
        Status.ASLEEP,
        Status.NEXT,
    ]


def test_outcome_of_another_day_does_not_count() -> None:
    yesterday = at(18) - datetime.timedelta(days=1)

    assert statuses(at(17), {(yesterday, ATHLETES): Status.PLAYED}) == [
        Status.NEXT,
        Status.WAITING,
    ]


def test_outcome_of_what_was_at_that_time_before_does_not_count() -> None:
    # The slot at 19:00 held another announcement when it came due.
    outcomes = {(at(19), "Salon kapanıyor."): Status.PLAYED}

    assert statuses(at(19, 30), outcomes) == [Status.PASSED, Status.PASSED]


def test_custom_announcement_that_lost_todays_slot_is_listed_after_the_winner() -> None:
    reminder = CustomAnnouncement("Salon kapanıyor.", SIX_PM, frozenset({0, 2}))
    timetable = build_timetable([AYSE], [reminder], "{ad}")

    assert todays_rows(timetable, at(8), {}) == (
        Row(SIX_PM, False, "Ayşe Yılmaz", Status.NEXT),
        Row(SIX_PM, True, "Salon kapanıyor.", Status.CONFLICT),
    )
    # On Wednesday the slot is free, so the announcement is simply due.
    wednesday = at(8) + datetime.timedelta(days=2)
    assert todays_rows(timetable, wednesday, {}) == (
        Row(SIX_PM, True, "Salon kapanıyor.", Status.NEXT),
    )


@pytest.mark.parametrize(
    ("playback", "status"),
    [
        (Playback(("a", "b"), (), (), False), Status.PLAYED),
        (Playback(("a", "b"), ("a",), (), False), Status.PARTLY_PLAYED),
        (Playback(("a", "b"), (), ("b",), False), Status.PARTLY_PLAYED),
        (Playback(("a", "b"), ("a", "b"), (), False), Status.NO_SPEECH),
        (Playback(("a", "b"), (), ("a", "b"), False), Status.ASLEEP),
        (Playback(("a", "b"), ("a",), ("b",), False), Status.ASLEEP),
        (Playback(("a", "b"), (), (), True), Status.NO_OUTPUT),
        (Playback(("a", "b"), ("a",), (), True), Status.NO_OUTPUT),
    ],
)
def test_outcome_status(playback: Playback, status: Status) -> None:
    assert outcome_status(playback) is status


def test_slot_built_by_hand_without_names_counts_as_custom() -> None:
    timetable = Timetable((Slot(0, NINE, ("Duyuru",)),))

    assert todays_rows(timetable, at(8), {}) == (
        Row(NINE, True, "Duyuru", Status.NEXT),
    )
