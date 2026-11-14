import dataclasses
import datetime

from announcer.models import AthleteEntry, Change, CustomAnnouncement, Kind
from announcer.template import DEFAULT_END_TEMPLATE
from announcer.timetable import (
    Conflict,
    Part,
    Slot,
    SlotHolder,
    build_timetable,
    delayed,
    find_conflicts,
    part_id,
    set_for,
)

TEMPLATE = "{ad_soyad}, hoş geldiniz."
END_TEMPLATE = "{ad_soyad}, görüşmek üzere."
MONDAY, TUESDAY, WEDNESDAY, SUNDAY = 0, 1, 2, 6
SIX_PM = datetime.time(18, 0)
SEVEN_PM = datetime.time(19, 0)
EIGHT_PM = datetime.time(20, 0)

THIS_MONDAY = datetime.date(2026, 11, 9)
THIS_TUESDAY = THIS_MONDAY + datetime.timedelta(days=1)
THIS_WEDNESDAY = THIS_MONDAY + datetime.timedelta(days=2)
NEXT_MONDAY = THIS_MONDAY + datetime.timedelta(days=7)

AYSE = AthleteEntry("Ayşe Yılmaz", MONDAY, SIX_PM)
MEHMET = AthleteEntry("Mehmet Demir", MONDAY, SIX_PM)
ZEYNEP = AthleteEntry("Zeynep Kaya", TUESDAY, SEVEN_PM)
CAN = AthleteEntry("Can Öz", MONDAY, SEVEN_PM)


def custom(text: str, time: datetime.time, *weekdays: int) -> CustomAnnouncement:
    return (
        CustomAnnouncement(text, time, frozenset(weekdays))
        if weekdays
        else CustomAnnouncement(text, time)
    )


def welcome(name: str, delay: int = 0) -> Part:
    """The start of the session of ``name``, as TEMPLATE says it."""
    return Part(Kind.START, f"{name}, hoş geldiniz.", name, delay)


def goodbye(name: str, delay: int = 0) -> Part:
    """The end of the session of ``name``, as END_TEMPLATE says it."""
    return Part(Kind.END, f"{name}, görüşmek üzere.", name, delay)


def said(text: str, delay: int = 0) -> Part:
    return Part(Kind.CUSTOM, text, delay=delay)


def skipped(part: Part) -> Part:
    return dataclasses.replace(part, skipped=True)


def edited(part: Part) -> Part:
    return dataclasses.replace(part, edited=True)


def moved(part: Part) -> Part:
    """``part`` edited to a time of its own."""
    return dataclasses.replace(part, edited=True, moved=True)


def moment(date: datetime.date, time: datetime.time) -> datetime.datetime:
    return datetime.datetime.combine(date, time)


def test_athletes_who_start_together_have_a_part_each_in_file_order() -> None:
    timetable = build_timetable([MEHMET, ZEYNEP, AYSE], [], TEMPLATE)

    assert timetable.slots == (
        Slot(MONDAY, SIX_PM, (welcome("Mehmet Demir"), welcome("Ayşe Yılmaz"))),
        Slot(TUESDAY, SEVEN_PM, (welcome("Zeynep Kaya"),)),
    )
    assert timetable.slots[0].texts == (
        "Mehmet Demir, hoş geldiniz.",
        "Ayşe Yılmaz, hoş geldiniz.",
    )
    assert timetable.conflicts == ()
    slot = timetable.slots[0]
    assert [part_id(THIS_MONDAY, slot, part) for part in slot.parts] == [
        (moment(THIS_MONDAY, SIX_PM), Kind.START, "Mehmet Demir"),
        (moment(THIS_MONDAY, SIX_PM), Kind.START, "Ayşe Yılmaz"),
    ]


def test_slots_are_ordered_by_weekday_then_time() -> None:
    entries = [
        AthleteEntry("D", SUNDAY, datetime.time(9, 0)),
        AthleteEntry("B", MONDAY, datetime.time(18, 30)),
        AthleteEntry("C", TUESDAY, datetime.time(7, 0)),
        AthleteEntry("A", MONDAY, datetime.time(9, 0)),
    ]

    timetable = build_timetable(entries, [], "{ad_soyad}")

    assert [slot.texts for slot in timetable.slots] == [("A",), ("B",), ("C",), ("D",)]
    assert [slot.texts for slot in timetable.slots_on(THIS_MONDAY)] == [("A",), ("B",)]
    assert timetable.slots_on(THIS_WEDNESDAY) == ()
    assert timetable.slot_at(THIS_TUESDAY, datetime.time(7, 0)) == timetable.slots[2]
    assert timetable.slot_at(THIS_TUESDAY, datetime.time(9, 0)) is None
    assert timetable.all_slots() == timetable.slots


def test_name_repeated_within_a_slot_is_said_once() -> None:
    timetable = build_timetable(
        [AYSE, MEHMET, AYSE], [], "{ad_soyad}", end_template="{ad_soyad}"
    )

    def parts(kind: Kind) -> tuple[Part, ...]:
        return tuple(Part(kind, name, name) for name in ("Ayşe Yılmaz", "Mehmet Demir"))

    assert timetable.slots == (
        Slot(MONDAY, SIX_PM, parts(Kind.START)),
        Slot(MONDAY, SEVEN_PM, parts(Kind.END)),
    )


def test_custom_announcement_gets_a_slot_on_each_of_its_weekdays() -> None:
    reminder = custom("Su içmeyi unutmayın.", SEVEN_PM, WEDNESDAY, MONDAY)

    timetable = build_timetable([AYSE], [reminder], TEMPLATE)

    assert timetable.slots == (
        Slot(MONDAY, SIX_PM, (welcome("Ayşe Yılmaz"),)),
        Slot(MONDAY, SEVEN_PM, (said("Su içmeyi unutmayın."),)),
        Slot(WEDNESDAY, SEVEN_PM, (said("Su içmeyi unutmayın."),)),
    )
    assert timetable.conflicts == ()


def test_custom_announcement_text_is_not_treated_as_a_template() -> None:
    literal = custom("{ad_soyad} yazan duyuru", SEVEN_PM, MONDAY)

    timetable = build_timetable([], [literal], TEMPLATE)

    assert timetable.slots == (
        Slot(MONDAY, SEVEN_PM, (said("{ad_soyad} yazan duyuru"),)),
    )
    slot = timetable.slots[0]
    assert part_id(THIS_MONDAY, slot, slot.parts[0]) == (
        moment(THIS_MONDAY, SEVEN_PM),
        Kind.CUSTOM,
        "",
    )


def test_athletes_win_a_slot_that_a_custom_announcement_also_wants() -> None:
    reminder = custom("Su içmeyi unutmayın.", SIX_PM, MONDAY, TUESDAY)

    timetable = build_timetable([AYSE, ZEYNEP], [reminder], TEMPLATE)

    assert timetable.slots == (
        Slot(MONDAY, SIX_PM, (welcome("Ayşe Yılmaz"),)),
        Slot(TUESDAY, SIX_PM, (said("Su içmeyi unutmayın."),)),
        Slot(TUESDAY, SEVEN_PM, (welcome("Zeynep Kaya"),)),
    )
    assert timetable.conflicts == (Conflict(reminder, MONDAY, SlotHolder.ATHLETES),)


def test_earlier_custom_announcement_wins_over_a_later_one() -> None:
    first = custom("Birinci", SEVEN_PM, MONDAY, TUESDAY)
    second = custom("İkinci", SEVEN_PM, TUESDAY, WEDNESDAY)

    timetable = build_timetable([], [first, second], TEMPLATE)

    assert timetable.slots == (
        Slot(MONDAY, SEVEN_PM, (said("Birinci"),)),
        Slot(TUESDAY, SEVEN_PM, (said("Birinci"),)),
        Slot(WEDNESDAY, SEVEN_PM, (said("İkinci"),)),
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


def test_find_conflicts_ignores_the_end_of_a_session() -> None:
    candidate = custom("Yeni", SEVEN_PM, MONDAY)

    assert find_conflicts(candidate, [AYSE], []) == ()
    assert find_conflicts(candidate, [AYSE, CAN], []) == (
        Conflict(candidate, MONDAY, SlotHolder.ATHLETES),
    )


def test_delay_moves_every_slot_and_keeps_what_was_in_conflict() -> None:
    reminder = custom("Su içmeyi unutmayın.", SIX_PM, MONDAY, TUESDAY)

    timetable = build_timetable([AYSE, ZEYNEP], [reminder], TEMPLATE, delay=2)

    late_six, late_seven = datetime.time(18, 2), datetime.time(19, 2)
    assert timetable.slots == (
        Slot(MONDAY, late_six, (welcome("Ayşe Yılmaz", delay=2),)),
        Slot(TUESDAY, late_six, (said("Su içmeyi unutmayın.", delay=2),)),
        Slot(TUESDAY, late_seven, (welcome("Zeynep Kaya", delay=2),)),
    )
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
    assert [set_for(THIS_MONDAY, slot, slot.parts[0]) for slot in timetable.slots] == [
        datetime.datetime(2026, 11, 8, 23, 50),
        datetime.datetime(2026, 11, 8, 23, 55),
    ]


def test_slots_carry_the_speed_they_are_spoken_at() -> None:
    reminder = custom("Su içmeyi unutmayın.", SEVEN_PM, MONDAY)
    skip = Change(
        THIS_MONDAY,
        moment(THIS_MONDAY, SIX_PM),
        Kind.START,
        "Ayşe Yılmaz",
        skipped=True,
    )

    slow = build_timetable([AYSE], [reminder], TEMPLATE, speed=0.8, changes=[skip])

    assert [slot.speed for slot in slow.slots] == [0.8, 0.8]
    assert [slot.speed for slot in slow.slots_on(THIS_MONDAY)] == [0.8, 0.8]
    normal = build_timetable([AYSE], [reminder], TEMPLATE)
    assert [slot.speed for slot in normal.slots] == [1.0, 1.0]


def test_sessions_end_an_hour_after_they_start_with_an_end_template() -> None:
    timetable = build_timetable(
        [AYSE, MEHMET, ZEYNEP], [], TEMPLATE, end_template=END_TEMPLATE
    )

    assert timetable.slots == (
        Slot(MONDAY, SIX_PM, (welcome("Ayşe Yılmaz"), welcome("Mehmet Demir"))),
        Slot(MONDAY, SEVEN_PM, (goodbye("Ayşe Yılmaz"), goodbye("Mehmet Demir"))),
        Slot(TUESDAY, SEVEN_PM, (welcome("Zeynep Kaya"),)),
        Slot(TUESDAY, EIGHT_PM, (goodbye("Zeynep Kaya"),)),
    )


def test_default_end_template_says_goodbye_to_each_athlete_by_name() -> None:
    timetable = build_timetable([AYSE], [], TEMPLATE, end_template=DEFAULT_END_TEMPLATE)

    end = timetable.slot_at(THIS_MONDAY, SEVEN_PM)
    assert end is not None
    assert end.texts == ("Ayşe Yılmaz, antrenmanınız sona ermiştir. Görüşmek üzere.",)


def test_without_an_end_template_only_the_starts_are_announced() -> None:
    timetable = build_timetable([AYSE, ZEYNEP], [], TEMPLATE)

    assert timetable.slots == (
        Slot(MONDAY, SIX_PM, (welcome("Ayşe Yılmaz"),)),
        Slot(TUESDAY, SEVEN_PM, (welcome("Zeynep Kaya"),)),
    )


def test_delay_moves_the_end_of_a_session_too() -> None:
    timetable = build_timetable(
        [AYSE], [], TEMPLATE, delay=2, end_template=END_TEMPLATE
    )

    assert timetable.slots == (
        Slot(MONDAY, datetime.time(18, 2), (welcome("Ayşe Yılmaz", delay=2),)),
        Slot(MONDAY, datetime.time(19, 2), (goodbye("Ayşe Yılmaz", delay=2),)),
    )
    end = timetable.slots[1]
    assert part_id(THIS_MONDAY, end, end.parts[0]) == (
        moment(THIS_MONDAY, SEVEN_PM),
        Kind.END,
        "Ayşe Yılmaz",
    )


def test_delay_past_midnight_moves_an_end_to_the_next_day() -> None:
    late = AthleteEntry("Ayşe Yılmaz", SUNDAY, datetime.time(22, 55))
    later = AthleteEntry("Mehmet Demir", SUNDAY, datetime.time(23, 30))

    timetable = build_timetable(
        [late, later], [], TEMPLATE, delay=10, end_template=END_TEMPLATE
    )

    assert timetable.slots == (
        Slot(MONDAY, datetime.time(0, 5), (goodbye("Ayşe Yılmaz", delay=10),)),
        Slot(MONDAY, datetime.time(0, 40), (goodbye("Mehmet Demir", delay=10),)),
        Slot(SUNDAY, datetime.time(23, 5), (welcome("Ayşe Yılmaz", delay=10),)),
        Slot(SUNDAY, datetime.time(23, 40), (welcome("Mehmet Demir", delay=10),)),
    )
    end = timetable.slots[0]
    assert part_id(NEXT_MONDAY, end, end.parts[0]) == (
        datetime.datetime(2026, 11, 15, 23, 55),
        Kind.END,
        "Ayşe Yılmaz",
    )


def test_minute_where_sessions_end_and_others_start_says_goodbye_first() -> None:
    timetable = build_timetable(
        [AYSE, CAN, MEHMET], [], TEMPLATE, end_template=END_TEMPLATE
    )

    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(MONDAY, SIX_PM, (welcome("Ayşe Yılmaz"), welcome("Mehmet Demir"))),
        Slot(
            MONDAY,
            SEVEN_PM,
            (goodbye("Ayşe Yılmaz"), goodbye("Mehmet Demir"), welcome("Can Öz")),
        ),
        Slot(MONDAY, EIGHT_PM, (goodbye("Can Öz"),)),
    )
    assert timetable.slots[1].texts == (
        "Ayşe Yılmaz, görüşmek üzere.",
        "Mehmet Demir, görüşmek üzere.",
        "Can Öz, hoş geldiniz.",
    )


def test_back_to_back_sessions_of_one_athlete_end_and_start_in_one_minute() -> None:
    again = AthleteEntry("Ayşe Yılmaz", MONDAY, SEVEN_PM)

    timetable = build_timetable([AYSE, again], [], TEMPLATE, end_template=END_TEMPLATE)

    assert timetable.slots == (
        Slot(MONDAY, SIX_PM, (welcome("Ayşe Yılmaz"),)),
        Slot(MONDAY, SEVEN_PM, (goodbye("Ayşe Yılmaz"), welcome("Ayşe Yılmaz"))),
        Slot(MONDAY, EIGHT_PM, (goodbye("Ayşe Yılmaz"),)),
    )
    slot = timetable.slots[1]
    assert [part_id(THIS_MONDAY, slot, part) for part in slot.parts] == [
        (moment(THIS_MONDAY, SEVEN_PM), Kind.END, "Ayşe Yılmaz"),
        (moment(THIS_MONDAY, SEVEN_PM), Kind.START, "Ayşe Yılmaz"),
    ]


def test_change_for_one_session_of_an_athlete_leaves_her_other_session_alone() -> None:
    again = AthleteEntry("Ayşe Yılmaz", MONDAY, SEVEN_PM)
    skip = Change(
        THIS_MONDAY,
        moment(THIS_MONDAY, SEVEN_PM),
        Kind.START,
        "Ayşe Yılmaz",
        skipped=True,
    )

    timetable = build_timetable(
        [AYSE, again], [], TEMPLATE, end_template=END_TEMPLATE, changes=[skip]
    )

    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(MONDAY, SIX_PM, (welcome("Ayşe Yılmaz"),)),
        Slot(
            MONDAY,
            SEVEN_PM,
            (goodbye("Ayşe Yılmaz"), skipped(welcome("Ayşe Yılmaz"))),
        ),
        Slot(MONDAY, EIGHT_PM, (goodbye("Ayşe Yılmaz"),)),
    )


def test_custom_announcement_where_sessions_only_end_is_read_after_the_end() -> None:
    reminder = custom("Su içmeyi unutmayın.", SEVEN_PM, MONDAY, TUESDAY)
    second = custom("İkinci", SEVEN_PM, MONDAY)

    timetable = build_timetable(
        [AYSE], [reminder, second], TEMPLATE, end_template=END_TEMPLATE
    )

    assert timetable.slots == (
        Slot(MONDAY, SIX_PM, (welcome("Ayşe Yılmaz"),)),
        Slot(
            MONDAY,
            SEVEN_PM,
            (goodbye("Ayşe Yılmaz"), said("Su içmeyi unutmayın.")),
        ),
        Slot(TUESDAY, SEVEN_PM, (said("Su içmeyi unutmayın."),)),
    )
    # The end holds no minute, but the custom announcement it is read with does.
    assert timetable.conflicts == (
        Conflict(second, MONDAY, SlotHolder.CUSTOM_ANNOUNCEMENT),
    )


def test_custom_announcement_at_a_start_conflicts_though_sessions_also_end() -> None:
    reminder = custom("Su içmeyi unutmayın.", SEVEN_PM, MONDAY)

    timetable = build_timetable(
        [AYSE, CAN], [reminder], TEMPLATE, end_template=END_TEMPLATE
    )

    assert timetable.slot_at(THIS_MONDAY, SEVEN_PM) == Slot(
        MONDAY, SEVEN_PM, (goodbye("Ayşe Yılmaz"), welcome("Can Öz"))
    )
    assert timetable.conflicts == (Conflict(reminder, MONDAY, SlotHolder.ATHLETES),)


def test_skipped_part_is_marked_on_its_day_only() -> None:
    # The change names the moment that the part was set for, before the delay.
    skip = Change(
        THIS_MONDAY,
        moment(THIS_MONDAY, SIX_PM),
        Kind.START,
        "Ayşe Yılmaz",
        skipped=True,
    )

    timetable = build_timetable(
        [AYSE, MEHMET],
        [],
        TEMPLATE,
        delay=2,
        end_template=END_TEMPLATE,
        changes=[skip],
    )

    starts = (welcome("Ayşe Yılmaz", delay=2), welcome("Mehmet Demir", delay=2))
    ends = (goodbye("Ayşe Yılmaz", delay=2), goodbye("Mehmet Demir", delay=2))
    late_six, late_seven = datetime.time(18, 2), datetime.time(19, 2)
    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(MONDAY, late_six, (skipped(starts[0]), starts[1])),
        Slot(MONDAY, late_seven, ends),
    )
    assert timetable.slots_on(NEXT_MONDAY) == (
        Slot(MONDAY, late_six, starts),
        Slot(MONDAY, late_seven, ends),
    )


def test_change_for_one_athlete_leaves_the_others_of_the_minute_alone() -> None:
    changes = [
        Change(
            THIS_MONDAY,
            moment(THIS_MONDAY, SIX_PM),
            Kind.START,
            "Mehmet Demir",
            skipped=True,
        ),
        Change(
            THIS_MONDAY,
            moment(THIS_MONDAY, SEVEN_PM),
            Kind.END,
            "Ayşe Yılmaz",
            text="{ad_soyad}, yarın görüşürüz.",
        ),
    ]

    timetable = build_timetable(
        [AYSE, MEHMET], [], TEMPLATE, end_template=END_TEMPLATE, changes=changes
    )

    reworded = Part(
        Kind.END, "Ayşe Yılmaz, yarın görüşürüz.", "Ayşe Yılmaz", edited=True
    )
    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(
            MONDAY,
            SIX_PM,
            (welcome("Ayşe Yılmaz"), skipped(welcome("Mehmet Demir"))),
        ),
        Slot(MONDAY, SEVEN_PM, (reworded, goodbye("Mehmet Demir"))),
    )


def test_skipped_athletes_keep_their_parts_so_no_minute_is_dropped() -> None:
    changes = [
        Change(THIS_MONDAY, moment(THIS_MONDAY, SIX_PM), Kind.START, name, skipped=True)
        for name in ("Ayşe Yılmaz", "Mehmet Demir")
    ]

    timetable = build_timetable(
        [AYSE, MEHMET, CAN], [], TEMPLATE, end_template=END_TEMPLATE, changes=changes
    )

    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(
            MONDAY,
            SIX_PM,
            (skipped(welcome("Ayşe Yılmaz")), skipped(welcome("Mehmet Demir"))),
        ),
        Slot(
            MONDAY,
            SEVEN_PM,
            (goodbye("Ayşe Yılmaz"), goodbye("Mehmet Demir"), welcome("Can Öz")),
        ),
        Slot(MONDAY, EIGHT_PM, (goodbye("Can Öz"),)),
    )
    assert len(timetable.slots_on(NEXT_MONDAY)) == 3


def test_moved_part_joins_the_minute_it_is_moved_to_in_the_order_of_kinds() -> None:
    half_past = datetime.time(18, 30)
    reminder = custom("Su içmeyi unutmayın.", half_past, MONDAY)
    # To the minute that the delay put the end of one session and the start
    # of the next in: the time is the day's, so the delay is not added to it.
    move = Change(
        THIS_MONDAY,
        moment(THIS_MONDAY, half_past),
        Kind.CUSTOM,
        time=datetime.time(19, 2),
    )

    timetable = build_timetable(
        [AYSE, CAN],
        [reminder],
        TEMPLATE,
        delay=2,
        end_template=END_TEMPLATE,
        changes=[move],
    )

    moved_part = moved(said("Su içmeyi unutmayın.", delay=32))
    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(MONDAY, datetime.time(18, 2), (welcome("Ayşe Yılmaz", delay=2),)),
        Slot(
            MONDAY,
            datetime.time(19, 2),
            (goodbye("Ayşe Yılmaz", delay=2), welcome("Can Öz", delay=2), moved_part),
        ),
        Slot(MONDAY, datetime.time(20, 2), (goodbye("Can Öz", delay=2),)),
    )
    slot = timetable.slots_on(THIS_MONDAY)[1]
    assert part_id(THIS_MONDAY, slot, moved_part) == (
        moment(THIS_MONDAY, half_past),
        Kind.CUSTOM,
        "",
    )


def test_athletes_moved_into_a_minute_are_read_in_the_order_of_their_sessions() -> None:
    earlier = AthleteEntry("Ayşe Yılmaz", MONDAY, SIX_PM)
    later = AthleteEntry("Mehmet Demir", MONDAY, EIGHT_PM)
    changes = [
        Change(
            THIS_MONDAY,
            moment(THIS_MONDAY, EIGHT_PM),
            Kind.START,
            "Mehmet Demir",
            time=SEVEN_PM,
        ),
        Change(
            THIS_MONDAY,
            moment(THIS_MONDAY, SIX_PM),
            Kind.START,
            "Ayşe Yılmaz",
            time=SEVEN_PM,
        ),
    ]

    timetable = build_timetable([later, CAN, earlier], [], TEMPLATE, changes=changes)

    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(
            MONDAY,
            SEVEN_PM,
            (
                moved(welcome("Ayşe Yılmaz", delay=60)),
                welcome("Can Öz"),
                moved(welcome("Mehmet Demir", delay=-60)),
            ),
        ),
    )


def test_moving_one_athlete_leaves_the_other_of_the_minute_in_place() -> None:
    move = Change(
        THIS_MONDAY,
        moment(THIS_MONDAY, SIX_PM),
        Kind.START,
        "Ayşe Yılmaz",
        time=datetime.time(18, 10),
    )

    timetable = build_timetable([AYSE, MEHMET], [], TEMPLATE, changes=[move])

    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(MONDAY, SIX_PM, (welcome("Mehmet Demir"),)),
        Slot(MONDAY, datetime.time(18, 10), (moved(welcome("Ayşe Yılmaz", delay=10)),)),
    )


def test_moved_part_keeps_its_time_whatever_the_delay() -> None:
    move = Change(
        THIS_MONDAY,
        moment(THIS_MONDAY, SIX_PM),
        Kind.START,
        "Ayşe Yılmaz",
        time=datetime.time(18, 10),
    )

    timetable = build_timetable([AYSE, MEHMET], [], TEMPLATE, delay=5, changes=[move])

    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(MONDAY, datetime.time(18, 5), (welcome("Mehmet Demir", delay=5),)),
        Slot(MONDAY, datetime.time(18, 10), (moved(welcome("Ayşe Yılmaz", delay=10)),)),
    )


def test_part_moved_earlier_still_tells_the_moment_it_was_set_for() -> None:
    early = Change(
        THIS_MONDAY,
        moment(THIS_MONDAY, SEVEN_PM),
        Kind.END,
        "Ayşe Yılmaz",
        time=datetime.time(18, 45),
    )

    timetable = build_timetable(
        [AYSE], [], TEMPLATE, end_template=END_TEMPLATE, changes=[early]
    )

    moved_part = moved(goodbye("Ayşe Yılmaz", delay=-15))
    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(MONDAY, SIX_PM, (welcome("Ayşe Yılmaz"),)),
        Slot(MONDAY, datetime.time(18, 45), (moved_part,)),
    )
    slot = timetable.slots_on(THIS_MONDAY)[1]
    assert part_id(THIS_MONDAY, slot, moved_part) == (
        moment(THIS_MONDAY, SEVEN_PM),
        Kind.END,
        "Ayşe Yılmaz",
    )


def test_reworded_part_of_an_athlete_says_the_name_typed_into_it() -> None:
    changes = [
        Change(
            THIS_MONDAY,
            moment(THIS_MONDAY, SIX_PM),
            Kind.START,
            "Ayşe Yılmaz",
            text="{ad_soyad}, salon bugün erken kapanıyor.",
        ),
        Change(
            THIS_MONDAY,
            moment(THIS_MONDAY, SEVEN_PM),
            Kind.START,
            "Can Öz",
            text="Hoş geldin Can.",
        ),
    ]

    timetable = build_timetable([AYSE, MEHMET, CAN], [], TEMPLATE, changes=changes)

    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(
            MONDAY,
            SIX_PM,
            (
                Part(
                    Kind.START,
                    "Ayşe Yılmaz, salon bugün erken kapanıyor.",
                    "Ayşe Yılmaz",
                    edited=True,
                ),
                welcome("Mehmet Demir"),
            ),
        ),
        Slot(
            MONDAY,
            SEVEN_PM,
            (Part(Kind.START, "Hoş geldin Can.", "Can Öz", edited=True),),
        ),
    )
    assert timetable.slots_on(NEXT_MONDAY) == (
        Slot(MONDAY, SIX_PM, (welcome("Ayşe Yılmaz"), welcome("Mehmet Demir"))),
        Slot(MONDAY, SEVEN_PM, (welcome("Can Öz"),)),
    )


def test_reworded_custom_announcement_says_the_new_text_that_day() -> None:
    reminder = custom("Su içmeyi unutmayın.", SEVEN_PM, MONDAY)
    reword = Change(
        THIS_MONDAY,
        moment(THIS_MONDAY, SEVEN_PM),
        Kind.CUSTOM,
        text="Salon bugün erken kapanıyor.",
    )

    timetable = build_timetable([], [reminder], TEMPLATE, changes=[reword])

    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(MONDAY, SEVEN_PM, (edited(said("Salon bugün erken kapanıyor.")),)),
    )
    assert timetable.slots_on(NEXT_MONDAY) == (
        Slot(MONDAY, SEVEN_PM, (said("Su içmeyi unutmayın."),)),
    )


def test_reworded_custom_announcement_is_said_as_it_is_written() -> None:
    reminder = custom("Su içmeyi unutmayın.", SEVEN_PM, MONDAY)
    reword = Change(
        THIS_MONDAY,
        moment(THIS_MONDAY, SEVEN_PM),
        Kind.CUSTOM,
        text="{ad_soyad} yazan duyuru",
    )

    timetable = build_timetable([], [reminder], TEMPLATE, changes=[reword])

    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(MONDAY, SEVEN_PM, (edited(said("{ad_soyad} yazan duyuru")),)),
    )


def test_changes_for_one_day_leave_the_week_and_the_other_days_alone() -> None:
    entries = [AYSE, ZEYNEP]
    changes = [
        Change(
            THIS_TUESDAY,
            moment(THIS_TUESDAY, SEVEN_PM),
            Kind.START,
            "Zeynep Kaya",
            skipped=True,
        ),
        Change(
            THIS_MONDAY,
            moment(THIS_MONDAY, SIX_PM),
            Kind.START,
            "Ayşe Yılmaz",
            time=datetime.time(18, 5),
        ),
    ]

    week = build_timetable(entries, [], TEMPLATE, end_template=END_TEMPLATE)
    timetable = build_timetable(
        entries, [], TEMPLATE, end_template=END_TEMPLATE, changes=changes
    )

    assert timetable.slots == week.slots
    assert [day for day, _ in timetable.days] == [THIS_MONDAY, THIS_TUESDAY]
    assert timetable.slots_on(THIS_MONDAY) != week.slots_on(THIS_MONDAY)
    assert timetable.slots_on(THIS_TUESDAY) != week.slots_on(THIS_TUESDAY)
    assert timetable.slots_on(NEXT_MONDAY) == week.slots_on(THIS_MONDAY)
    assert timetable.slots_on(THIS_WEDNESDAY) == ()


def test_change_that_matches_no_part_leaves_its_day_as_the_week_has_it() -> None:
    reminder = custom("Su içmeyi unutmayın.", SEVEN_PM, MONDAY)
    changes = [
        Change(
            THIS_MONDAY,
            moment(THIS_MONDAY, datetime.time(9, 0)),
            Kind.START,
            "Ayşe Yılmaz",
            skipped=True,
        ),
        # The moment of a start, but no end was set for it.
        Change(
            THIS_MONDAY,
            moment(THIS_MONDAY, SIX_PM),
            Kind.END,
            "Ayşe Yılmaz",
            skipped=True,
        ),
        # The moment and kind of a part, but for an athlete who is not in it.
        Change(
            THIS_MONDAY,
            moment(THIS_MONDAY, SIX_PM),
            Kind.START,
            "Zeynep Kaya",
            skipped=True,
        ),
        # Of an athlete's part, without the athlete's name.
        Change(THIS_MONDAY, moment(THIS_MONDAY, SIX_PM), Kind.START, skipped=True),
        # Of a custom announcement, with a name.
        Change(
            THIS_MONDAY,
            moment(THIS_MONDAY, SEVEN_PM),
            Kind.CUSTOM,
            "Ayşe Yılmaz",
            skipped=True,
        ),
    ]

    timetable = build_timetable(
        [AYSE], [reminder], TEMPLATE, end_template=END_TEMPLATE, changes=changes
    )

    assert timetable.slots_on(THIS_MONDAY) == timetable.slots_on(NEXT_MONDAY)


def test_change_finds_an_end_that_the_delay_moved_past_midnight() -> None:
    late = AthleteEntry("Ayşe Yılmaz", SUNDAY, datetime.time(22, 55))
    # The end is announced on the Monday, but was set for the Sunday before.
    skip = Change(
        NEXT_MONDAY,
        datetime.datetime(2026, 11, 15, 23, 55),
        Kind.END,
        "Ayşe Yılmaz",
        skipped=True,
    )

    timetable = build_timetable(
        [late], [], TEMPLATE, delay=10, end_template=END_TEMPLATE, changes=[skip]
    )

    end = goodbye("Ayşe Yılmaz", delay=10)
    assert timetable.slots_on(NEXT_MONDAY) == (
        Slot(MONDAY, datetime.time(0, 5), (skipped(end),)),
    )
    assert timetable.slots_on(THIS_MONDAY) == (
        Slot(MONDAY, datetime.time(0, 5), (end,)),
    )


def test_slot_at_reads_the_plan_of_the_date_it_is_given() -> None:
    move = Change(
        THIS_MONDAY,
        moment(THIS_MONDAY, SIX_PM),
        Kind.START,
        "Ayşe Yılmaz",
        time=datetime.time(18, 5),
    )

    timetable = build_timetable([AYSE], [], TEMPLATE, changes=[move])

    moved_slot = Slot(
        MONDAY, datetime.time(18, 5), (moved(welcome("Ayşe Yılmaz", delay=5)),)
    )
    week = Slot(MONDAY, SIX_PM, (welcome("Ayşe Yılmaz"),))
    assert timetable.slot_at(THIS_MONDAY, SIX_PM) is None
    assert timetable.slot_at(THIS_MONDAY, datetime.time(18, 5)) == moved_slot
    assert timetable.slot_at(NEXT_MONDAY, SIX_PM) == week
    assert timetable.slot_at(NEXT_MONDAY, datetime.time(18, 5)) is None
    assert timetable.slot_at(THIS_TUESDAY, SIX_PM) is None
    assert timetable.all_slots() == (week, moved_slot)
