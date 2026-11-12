import datetime

import pytest

from announcer.models import AthleteEntry, CustomAnnouncement
from announcer.player import Playback
from announcer.timetable import Timetable, build_timetable
from announcer.today import Row, Status, outcome_status, todays_rows
from support import at

SIX_PM, SEVEN_PM = datetime.time(18), datetime.time(19)

AYSE = AthleteEntry("Ayşe Yılmaz", 0, SIX_PM)
MEHMET = AthleteEntry("Mehmet Demir", 0, SIX_PM)
ZEYNEP = AthleteEntry("Zeynep Kaya", 1, SIX_PM)
WATER = CustomAnnouncement("Su içmeyi unutmayın.", SEVEN_PM)
TIMETABLE = build_timetable(
    [AYSE, MEHMET, ZEYNEP], [WATER], "{ad_soyad}, hoş geldiniz."
)

ATHLETES, REMINDER = "Ayşe Yılmaz, Mehmet Demir", "Su içmeyi unutmayın."


def statuses(
    now: datetime.datetime,
    outcomes: dict[tuple[datetime.datetime, str], Status] | None = None,
) -> list[Status]:
    return [row.status for row in todays_rows(TIMETABLE, now, outcomes or {})]


def test_rows_list_todays_slots_in_time_order() -> None:
    assert todays_rows(TIMETABLE, at(8), {}) == (
        Row(SIX_PM, ATHLETES, Status.NEXT),
        Row(SEVEN_PM, REMINDER, Status.WAITING),
    )


def test_rows_are_those_of_the_weekday_of_now() -> None:
    tuesday = at(8) + datetime.timedelta(days=1)

    assert [row.summary for row in todays_rows(TIMETABLE, tuesday, {})] == [
        "Zeynep Kaya",
        REMINDER,
    ]
    assert todays_rows(Timetable(), at(8), {}) == ()


def test_only_the_first_slot_to_come_is_next() -> None:
    assert statuses(at(17, 59, 59)) == [Status.NEXT, Status.WAITING]
    assert statuses(at(18, 1)) == [Status.PASSED, Status.NEXT]
    assert statuses(at(23)) == [Status.PASSED, Status.PASSED]


def test_slot_is_passed_once_it_is_too_late_to_announce_it() -> None:
    assert statuses(at(18, 0, 30)) == [Status.NEXT, Status.WAITING]
    assert statuses(at(18, 0, 31)) == [Status.PASSED, Status.NEXT]


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


def test_outcome_of_another_day_or_of_what_was_there_before_does_not_count() -> None:
    yesterday = at(18) - datetime.timedelta(days=1)
    outcomes = {
        (yesterday, ATHLETES): Status.PLAYED,
        (at(19), "Salon kapanıyor."): Status.PLAYED,
    }

    assert statuses(at(17), outcomes) == [Status.NEXT, Status.WAITING]
    assert statuses(at(19, 30), outcomes) == [Status.PASSED, Status.PASSED]


def test_custom_announcement_that_lost_todays_slot_is_listed_after_the_winner() -> None:
    reminder = CustomAnnouncement("Salon kapanıyor.", SIX_PM, frozenset({0, 2}))
    timetable = build_timetable([AYSE], [reminder], "{ad_soyad}")

    assert todays_rows(timetable, at(8), {}) == (
        Row(SIX_PM, "Ayşe Yılmaz", Status.NEXT),
        Row(SIX_PM, "Salon kapanıyor.", Status.CONFLICT),
    )
    wednesday = at(8) + datetime.timedelta(days=2)
    assert todays_rows(timetable, wednesday, {}) == (
        Row(SIX_PM, "Salon kapanıyor.", Status.NEXT),
    )


def test_rows_show_when_a_delayed_announcement_is_made() -> None:
    reminder = CustomAnnouncement("Salon kapanıyor.", SIX_PM, frozenset({0}))
    late = CustomAnnouncement("Salon kapandı.", datetime.time(23, 58), frozenset({6}))
    timetable = build_timetable([AYSE], [reminder, late], "{ad_soyad}", delay=3)
    three_past = datetime.time(18, 3)

    assert todays_rows(timetable, at(18, 2), {}) == (
        # From Sunday night, and the scheduler was not there for it.
        Row(datetime.time(0, 1), "Salon kapandı.", Status.PASSED),
        Row(three_past, "Ayşe Yılmaz", Status.NEXT),
        Row(three_past, "Salon kapanıyor.", Status.CONFLICT),
    )
    outcomes = {(at(18, 3), "Ayşe Yılmaz"): Status.PLAYED}
    assert todays_rows(timetable, at(18, 4), outcomes)[1].status is Status.PLAYED


def test_what_lost_its_slot_on_the_day_before_midnight_is_listed_after_it() -> None:
    late = AthleteEntry("Ayşe Yılmaz", 6, datetime.time(23, 58))
    reminder = CustomAnnouncement("Salon kapandı.", late.time, frozenset({6}))
    timetable = build_timetable([late], [reminder], "{ad_soyad}", delay=5)

    assert [row.status for row in todays_rows(timetable, at(0, 0), {})] == [
        Status.NEXT,
        Status.CONFLICT,
    ]
    sunday = at(8) + datetime.timedelta(days=6)
    assert todays_rows(timetable, sunday, {}) == ()


@pytest.mark.parametrize(
    ("status", "replayable"),
    [
        (Status.WAITING, False),
        (Status.NEXT, False),
        (Status.PLAYING, False),
        (Status.CONFLICT, False),
        (Status.PASSED, True),
        (Status.PLAYED, True),
        (Status.PARTLY_PLAYED, True),
        (Status.NO_SPEECH, True),
        (Status.ASLEEP, True),
        (Status.NO_OUTPUT, True),
    ],
)
def test_only_an_announcement_whose_time_has_come_can_be_replayed(
    status: Status, replayable: bool
) -> None:
    assert Row(SIX_PM, ATHLETES, status).replayable is replayable


@pytest.mark.parametrize(
    ("playback", "status"),
    [
        (Playback(("a", "b"), (), (), False), Status.PLAYED),
        (Playback(("a", "b"), ("a",), (), False), Status.PARTLY_PLAYED),
        (Playback(("a", "b"), (), ("b",), False), Status.PARTLY_PLAYED),
        (Playback(("a", "b"), ("a", "b"), (), False), Status.NO_SPEECH),
        (Playback(("a", "b"), (), ("a", "b"), False), Status.ASLEEP),
        (Playback(("a", "b"), ("a",), ("b",), False), Status.ASLEEP),
        (Playback(("a", "b"), ("a",), (), True), Status.NO_OUTPUT),
    ],
)
def test_outcome_status(playback: Playback, status: Status) -> None:
    assert outcome_status(playback) is status
