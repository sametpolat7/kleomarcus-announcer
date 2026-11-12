import datetime

from announcer.models import AthleteEntry, CustomAnnouncement
from announcer.timetable import (
    Conflict,
    Slot,
    SlotHolder,
    build_timetable,
    delayed,
    find_conflicts,
    set_for,
)

TEMPLATE = "{ad_soyad}, hoş geldiniz."
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


def test_athletes_who_start_together_share_a_slot_in_file_order() -> None:
    timetable = build_timetable([MEHMET, ZEYNEP, AYSE], [], TEMPLATE)

    assert timetable.slots == (
        Slot(
            MONDAY,
            SIX_PM,
            ("Mehmet Demir, hoş geldiniz.", "Ayşe Yılmaz, hoş geldiniz."),
            ("Mehmet Demir", "Ayşe Yılmaz"),
        ),
        Slot(TUESDAY, SEVEN_PM, ("Zeynep Kaya, hoş geldiniz.",), ("Zeynep Kaya",)),
    )
    assert timetable.conflicts == ()


def test_slots_are_ordered_by_weekday_then_time() -> None:
    entries = [
        AthleteEntry("D", SUNDAY, datetime.time(9, 0)),
        AthleteEntry("B", MONDAY, datetime.time(18, 30)),
        AthleteEntry("C", TUESDAY, datetime.time(7, 0)),
        AthleteEntry("A", MONDAY, datetime.time(9, 0)),
    ]

    timetable = build_timetable(entries, [], "{ad_soyad}")

    assert [slot.texts for slot in timetable.slots] == [("A",), ("B",), ("C",), ("D",)]
    assert [slot.texts for slot in timetable.slots_on(MONDAY)] == [("A",), ("B",)]
    assert timetable.slots_on(WEDNESDAY) == ()
    assert timetable.slot_at(TUESDAY, datetime.time(7, 0)) == timetable.slots[2]
    assert timetable.slot_at(TUESDAY, datetime.time(9, 0)) is None


def test_name_repeated_within_a_slot_is_said_once() -> None:
    timetable = build_timetable([AYSE, MEHMET, AYSE], [], "{ad_soyad}")

    names = ("Ayşe Yılmaz", "Mehmet Demir")
    assert timetable.slots == (Slot(MONDAY, SIX_PM, names, names),)


def test_custom_announcement_gets_a_slot_on_each_of_its_weekdays() -> None:
    reminder = custom("Su içmeyi unutmayın.", SEVEN_PM, WEDNESDAY, MONDAY)

    timetable = build_timetable([AYSE], [reminder], TEMPLATE)

    assert timetable.slots == (
        Slot(MONDAY, SIX_PM, ("Ayşe Yılmaz, hoş geldiniz.",), ("Ayşe Yılmaz",)),
        Slot(MONDAY, SEVEN_PM, ("Su içmeyi unutmayın.",)),
        Slot(WEDNESDAY, SEVEN_PM, ("Su içmeyi unutmayın.",)),
    )
    assert timetable.conflicts == ()


def test_custom_announcement_text_is_not_treated_as_a_template() -> None:
    literal = custom("{ad_soyad} yazan duyuru", SEVEN_PM, MONDAY)

    timetable = build_timetable([], [literal], TEMPLATE)

    assert timetable.slots == (Slot(MONDAY, SEVEN_PM, ("{ad_soyad} yazan duyuru",)),)


def test_athletes_win_a_slot_that_a_custom_announcement_also_wants() -> None:
    reminder = custom("Su içmeyi unutmayın.", SIX_PM, MONDAY, TUESDAY)

    timetable = build_timetable([AYSE, ZEYNEP], [reminder], TEMPLATE)

    assert timetable.slots == (
        Slot(MONDAY, SIX_PM, ("Ayşe Yılmaz, hoş geldiniz.",), ("Ayşe Yılmaz",)),
        Slot(TUESDAY, SIX_PM, ("Su içmeyi unutmayın.",)),
        Slot(TUESDAY, SEVEN_PM, ("Zeynep Kaya, hoş geldiniz.",), ("Zeynep Kaya",)),
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


def test_no_conflicts_for_a_free_slot() -> None:
    candidate = custom("Yeni", datetime.time(18, 1), MONDAY, WEDNESDAY)
    other = custom("Eski", SIX_PM, WEDNESDAY)

    assert find_conflicts(candidate, [AYSE, ZEYNEP], [other]) == ()


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


def test_delay_moves_every_slot_and_keeps_what_was_in_conflict() -> None:
    reminder = custom("Su içmeyi unutmayın.", SIX_PM, MONDAY, TUESDAY)

    timetable = build_timetable([AYSE, ZEYNEP], [reminder], TEMPLATE, delay=2)
    slots = timetable.slots

    late_six, late_seven = datetime.time(18, 2), datetime.time(19, 2)
    assert [(slot.weekday, slot.time, slot.names, slot.delay) for slot in slots] == [
        (MONDAY, late_six, ("Ayşe Yılmaz",), 2),
        (TUESDAY, late_six, (), 2),
        (TUESDAY, late_seven, ("Zeynep Kaya",), 2),
    ]
    # A conflict names the time that the announcement was set for.
    assert timetable.conflicts == (Conflict(reminder, MONDAY, SlotHolder.ATHLETES),)
    assert timetable.delay == 2


def test_delay_past_midnight_moves_a_slot_to_the_next_day() -> None:
    late = AthleteEntry("Ayşe Yılmaz", SUNDAY, datetime.time(23, 55))
    closing = custom("Salon kapandı.", datetime.time(23, 50), SUNDAY)

    timetable = build_timetable([late], [closing], "{ad_soyad}", delay=10)

    assert [(slot.weekday, slot.time, slot.texts) for slot in timetable.slots] == [
        (MONDAY, datetime.time(0, 0), ("Salon kapandı.",)),
        (MONDAY, datetime.time(0, 5), ("Ayşe Yılmaz",)),
    ]
    assert delayed(SUNDAY, datetime.time(23, 59), 1) == (MONDAY, datetime.time(0, 0))
    assert delayed(TUESDAY, SIX_PM, 0) == (TUESDAY, SIX_PM)
    # The Monday whose first minutes these slots are in: what they were set
    # for is on the Sunday before.
    monday = datetime.date(2026, 11, 9)
    assert [set_for(monday, slot) for slot in timetable.slots] == [
        datetime.datetime(2026, 11, 8, 23, 50),
        datetime.datetime(2026, 11, 8, 23, 55),
    ]


def test_slots_carry_the_speed_they_are_spoken_at() -> None:
    reminder = custom("Su içmeyi unutmayın.", SEVEN_PM, MONDAY)

    slow = build_timetable([AYSE], [reminder], TEMPLATE, speed=0.8)

    assert [slot.speed for slot in slow.slots] == [0.8, 0.8]
    normal = build_timetable([AYSE], [reminder], TEMPLATE)
    assert [slot.speed for slot in normal.slots] == [1.0, 1.0]
