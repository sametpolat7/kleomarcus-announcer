"""Tests for building the timetable and for the conflict rules."""

import datetime

from announcer.models import AthleteEntry, CustomAnnouncement
from announcer.timetable import (
    Conflict,
    Slot,
    SlotHolder,
    Timetable,
    build_timetable,
    find_conflicts,
)

TEMPLATE = "{ad}, hoş geldiniz."
MONDAY, TUESDAY, WEDNESDAY, SUNDAY = 0, 1, 2, 6
SIX_PM = datetime.time(18, 0)
SEVEN_PM = datetime.time(19, 0)

AYSE = AthleteEntry("Ayşe Yılmaz", MONDAY, SIX_PM)
MEHMET = AthleteEntry("Mehmet Demir", MONDAY, SIX_PM)
ZEYNEP = AthleteEntry("Zeynep Kaya", TUESDAY, SEVEN_PM)


def custom(text: str, time: datetime.time, *weekdays: int) -> CustomAnnouncement:
    return (
        CustomAnnouncement(text, time, frozenset(weekdays))
        if weekdays
        else CustomAnnouncement(text, time)
    )


def test_empty_timetable() -> None:
    assert build_timetable([], [], TEMPLATE) == Timetable(slots=(), conflicts=())
    assert Timetable() == Timetable(slots=(), conflicts=())


def test_athletes_who_start_together_share_a_slot_in_file_order() -> None:
    timetable = build_timetable([MEHMET, ZEYNEP, AYSE], [], TEMPLATE)

    assert timetable.slots == (
        Slot(
            MONDAY,
            SIX_PM,
            ("Mehmet Demir, hoş geldiniz.", "Ayşe Yılmaz, hoş geldiniz."),
        ),
        Slot(TUESDAY, SEVEN_PM, ("Zeynep Kaya, hoş geldiniz.",)),
    )
    assert timetable.conflicts == ()


def test_slots_are_ordered_by_weekday_then_time() -> None:
    entries = [
        AthleteEntry("D", SUNDAY, datetime.time(9, 0)),
        AthleteEntry("B", MONDAY, datetime.time(18, 30)),
        AthleteEntry("C", TUESDAY, datetime.time(7, 0)),
        AthleteEntry("A", MONDAY, datetime.time(9, 0)),
    ]

    timetable = build_timetable(entries, [], "{ad}")

    assert [slot.texts for slot in timetable.slots] == [("A",), ("B",), ("C",), ("D",)]


def test_name_repeated_within_a_slot_is_said_once() -> None:
    timetable = build_timetable([AYSE, MEHMET, AYSE], [], "{ad}")

    assert timetable.slots == (Slot(MONDAY, SIX_PM, ("Ayşe Yılmaz", "Mehmet Demir")),)


def test_same_athlete_in_two_slots_is_said_in_both() -> None:
    later = AthleteEntry(AYSE.name, MONDAY, SEVEN_PM)

    timetable = build_timetable([AYSE, later], [], "{ad}")

    assert timetable.slots == (
        Slot(MONDAY, SIX_PM, ("Ayşe Yılmaz",)),
        Slot(MONDAY, SEVEN_PM, ("Ayşe Yılmaz",)),
    )


def test_custom_announcement_gets_a_slot_on_each_of_its_weekdays() -> None:
    reminder = custom("Su içmeyi unutmayın.", SEVEN_PM, WEDNESDAY, MONDAY)

    timetable = build_timetable([AYSE], [reminder], TEMPLATE)

    assert timetable.slots == (
        Slot(MONDAY, SIX_PM, ("Ayşe Yılmaz, hoş geldiniz.",)),
        Slot(MONDAY, SEVEN_PM, ("Su içmeyi unutmayın.",)),
        Slot(WEDNESDAY, SEVEN_PM, ("Su içmeyi unutmayın.",)),
    )
    assert timetable.conflicts == ()


def test_custom_announcement_for_every_day_gets_seven_slots() -> None:
    closing = custom("Salon kapanıyor.", datetime.time(22, 0))

    timetable = build_timetable([], [closing], TEMPLATE)

    assert [slot.weekday for slot in timetable.slots] == list(range(7))
    assert {slot.texts for slot in timetable.slots} == {("Salon kapanıyor.",)}


def test_custom_announcement_text_is_not_treated_as_a_template() -> None:
    literal = custom("{ad} yazan duyuru", SEVEN_PM, MONDAY)

    timetable = build_timetable([], [literal], TEMPLATE)

    assert timetable.slots == (Slot(MONDAY, SEVEN_PM, ("{ad} yazan duyuru",)),)


def test_athletes_win_a_slot_that_a_custom_announcement_also_wants() -> None:
    reminder = custom("Su içmeyi unutmayın.", SIX_PM, MONDAY, TUESDAY)

    timetable = build_timetable([AYSE, ZEYNEP], [reminder], TEMPLATE)

    # Only Monday is lost; the announcement still plays on Tuesday.
    assert timetable.slots == (
        Slot(MONDAY, SIX_PM, ("Ayşe Yılmaz, hoş geldiniz.",)),
        Slot(TUESDAY, SIX_PM, ("Su içmeyi unutmayın.",)),
        Slot(TUESDAY, SEVEN_PM, ("Zeynep Kaya, hoş geldiniz.",)),
    )
    assert timetable.conflicts == (Conflict(reminder, MONDAY, SlotHolder.ATHLETES),)


def test_earlier_custom_announcement_wins_over_a_later_one() -> None:
    first = custom("Birinci", SEVEN_PM, MONDAY, TUESDAY)
    second = custom("İkinci", SEVEN_PM, TUESDAY, WEDNESDAY)

    timetable = build_timetable([], [first, second], TEMPLATE)

    assert timetable.slots == (
        Slot(MONDAY, SEVEN_PM, ("Birinci",)),
        Slot(TUESDAY, SEVEN_PM, ("Birinci",)),
        Slot(WEDNESDAY, SEVEN_PM, ("İkinci",)),
    )
    assert timetable.conflicts == (
        Conflict(second, TUESDAY, SlotHolder.CUSTOM_ANNOUNCEMENT),
    )


def test_conflicts_are_listed_per_announcement_then_weekday() -> None:
    first = custom("Birinci", SIX_PM)
    second = custom("İkinci", SIX_PM, TUESDAY, MONDAY)

    timetable = build_timetable([AYSE], [first, second], TEMPLATE)

    assert timetable.conflicts == (
        Conflict(first, MONDAY, SlotHolder.ATHLETES),
        Conflict(second, MONDAY, SlotHolder.ATHLETES),
        Conflict(second, TUESDAY, SlotHolder.CUSTOM_ANNOUNCEMENT),
    )


def test_a_minute_apart_is_not_a_conflict() -> None:
    reminder = custom("Su içmeyi unutmayın.", datetime.time(18, 1), MONDAY)

    timetable = build_timetable([AYSE], [reminder], TEMPLATE)

    assert len(timetable.slots) == 2
    assert timetable.conflicts == ()


def test_slot_at_finds_the_slot_of_a_weekday_and_minute() -> None:
    timetable = build_timetable([AYSE, ZEYNEP], [], "{ad}")

    assert timetable.slot_at(TUESDAY, SEVEN_PM) == Slot(
        TUESDAY, SEVEN_PM, ("Zeynep Kaya",)
    )
    assert timetable.slot_at(TUESDAY, SIX_PM) is None
    assert timetable.slot_at(WEDNESDAY, SEVEN_PM) is None


def test_no_conflicts_for_a_free_slot() -> None:
    candidate = custom("Yeni", SEVEN_PM, MONDAY, WEDNESDAY)
    other = custom("Eski", SIX_PM, WEDNESDAY)

    assert find_conflicts(candidate, [AYSE, ZEYNEP], [other]) == ()


def test_conflict_with_an_athlete_slot() -> None:
    candidate = custom("Yeni", SIX_PM, SUNDAY, MONDAY)

    assert find_conflicts(candidate, [AYSE, MEHMET], []) == (
        Conflict(candidate, MONDAY, SlotHolder.ATHLETES),
    )


def test_conflict_with_another_custom_announcement() -> None:
    candidate = custom("Yeni", SEVEN_PM, MONDAY, WEDNESDAY)
    other = custom("Eski", SEVEN_PM, WEDNESDAY, SUNDAY)

    assert find_conflicts(candidate, [], [other]) == (
        Conflict(candidate, WEDNESDAY, SlotHolder.CUSTOM_ANNOUNCEMENT),
    )


def test_every_day_conflicts_with_any_weekday_at_the_same_time() -> None:
    candidate = custom("Yeni", SIX_PM)
    other = custom("Eski", SIX_PM, SUNDAY)

    assert find_conflicts(candidate, [AYSE], [other]) == (
        Conflict(candidate, MONDAY, SlotHolder.ATHLETES),
        Conflict(candidate, SUNDAY, SlotHolder.CUSTOM_ANNOUNCEMENT),
    )


def test_slot_held_by_both_is_reported_as_held_by_athletes() -> None:
    candidate = custom("Yeni", SIX_PM, MONDAY)
    other = custom("Eski", SIX_PM, MONDAY)

    assert find_conflicts(candidate, [AYSE], [other]) == (
        Conflict(candidate, MONDAY, SlotHolder.ATHLETES),
    )


def test_announcement_saved_without_conflicts_gets_all_its_slots() -> None:
    existing = [custom("Eski", SIX_PM, WEDNESDAY)]
    candidate = custom("Yeni", SEVEN_PM, MONDAY, WEDNESDAY)
    assert find_conflicts(candidate, [AYSE], existing) == ()

    timetable = build_timetable([AYSE], [*existing, candidate], TEMPLATE)

    assert timetable.conflicts == ()
    assert timetable.slot_at(MONDAY, SEVEN_PM) == Slot(MONDAY, SEVEN_PM, ("Yeni",))
    assert timetable.slot_at(WEDNESDAY, SEVEN_PM) == Slot(
        WEDNESDAY, SEVEN_PM, ("Yeni",)
    )
