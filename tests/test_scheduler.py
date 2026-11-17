import dataclasses
import datetime
import threading
from collections.abc import Iterable, Iterator, Sequence

import pytest

from announcer.models import AthleteEntry, Change, CustomAnnouncement, Kind
from announcer.scheduler import (
    Scheduler,
    brought_forward,
    due_slot,
    missed_slots,
    untaken,
)
from announcer.timetable import Part, Slot, Timetable, build_timetable
from support import MONDAY, FakeClock

SUNDAY = MONDAY - datetime.timedelta(days=1)
TUESDAY = MONDAY + datetime.timedelta(days=1)
SIX_PM = datetime.time(18, 0)
FIVE_TO_SEVEN = datetime.time(18, 55)
SEVEN_PM = datetime.time(19, 0)

DRINK = "Su içmeyi unutmayın."
CLOSING = "Salon kapanıyor."
AYSE_STARTS = Part(Kind.START, "Ayşe Yılmaz, hoş geldiniz.", "Ayşe Yılmaz")
ZEYNEP_STARTS = Part(Kind.START, "Zeynep Kaya, hoş geldiniz.", "Zeynep Kaya")
ZEYNEP_ENDS = Part(Kind.END, "Zeynep Kaya, görüşmek üzere.", "Zeynep Kaya")
REMINDER = Part(Kind.CUSTOM, DRINK, custom_text=DRINK)

MONDAY_SIX = Slot(0, SIX_PM, (AYSE_STARTS,))
MONDAY_SIX_OH_ONE = Slot(0, datetime.time(18, 1), (REMINDER,))
TUESDAY_SIX = Slot(1, SIX_PM, (ZEYNEP_STARTS,))
TIMETABLE = Timetable((MONDAY_SIX, MONDAY_SIX_OH_ONE, TUESDAY_SIX))

# What ``planned`` and ``two_at_seven`` say.
AYSE_GOES = "Ayşe Yılmaz, görüşmek üzere."
ZEYNEP_COMES = "Zeynep Kaya, hoş geldiniz."
MEHMET_COMES = "Mehmet Demir, hoş geldiniz."


def at(
    hour: int, minute: int, second: int = 0, *, days_later: int = 0
) -> datetime.datetime:
    day = MONDAY + datetime.timedelta(days=days_later)
    return datetime.datetime.combine(day, datetime.time(hour, minute, second))


def every_second(
    first: datetime.datetime, last: datetime.datetime
) -> Iterator[datetime.datetime]:
    moment = first
    while moment <= last:
        yield moment
        moment += datetime.timedelta(seconds=1)


def ignore(date: datetime.date, slot: Slot) -> None:
    """Take a reported slot and do nothing with it."""


def skipped(part: Part) -> Part:
    return dataclasses.replace(part, skipped=True)


def said(slots: Iterable[Slot]) -> list[tuple[datetime.time, tuple[str, ...]]]:
    return [(slot.time, slot.texts) for slot in slots]


def make_scheduler(
    clock: FakeClock, timetable: Timetable = TIMETABLE
) -> tuple[Scheduler, list[Slot], list[tuple[datetime.date, Slot]]]:
    due: list[Slot] = []
    missed: list[tuple[datetime.date, Slot]] = []
    scheduler = Scheduler(
        timetable,
        lambda date, slot: due.append(slot),
        lambda date, slot: missed.append((date, slot)),
        clock,
    )
    scheduler.check()  # the first look, with which a scheduler begins
    return scheduler, due, missed


def planned(
    *changes: Change, start_delay: int = 0, end_delay: int = 0, ends: bool = True
) -> Timetable:
    """Ayşe's session ends at five to seven, as Zeynep's begins."""
    return build_timetable(
        [
            AthleteEntry("Ayşe Yılmaz", 0, SIX_PM),
            AthleteEntry("Zeynep Kaya", 0, FIVE_TO_SEVEN),
        ],
        [CustomAnnouncement(DRINK, datetime.time(18, 56), frozenset({0}))],
        "{ad_soyad}, hoş geldiniz.",
        start_delay=start_delay,
        end_delay=end_delay,
        end_template="{ad_soyad}, görüşmek üzere." if ends else None,
        changes=changes,
    )


def test_slot_is_due_for_the_first_half_of_its_minute() -> None:
    assert due_slot(TIMETABLE, at(18, 0, 0), ()) == MONDAY_SIX
    assert due_slot(TIMETABLE, at(18, 0, 30), ()) == MONDAY_SIX
    assert due_slot(TIMETABLE, at(18, 0, 31), ()) is None


def test_nothing_is_due_at_another_time_or_on_another_day() -> None:
    assert due_slot(TIMETABLE, at(17, 59, 59), ()) is None
    assert due_slot(TIMETABLE, at(18, 2), ()) is None
    assert due_slot(TIMETABLE, at(18, 0, days_later=1), ()) == TUESDAY_SIX
    assert due_slot(TIMETABLE, at(18, 1, days_later=1), ()) is None
    assert due_slot(TIMETABLE, at(18, 0, days_later=2), ()) is None


def test_slot_that_already_fired_is_not_due_again() -> None:
    fired = {(at(18, 0), Kind.START, "Ayşe Yılmaz")}
    ended = {(at(18, 0), Kind.END, "Ayşe Yılmaz")}
    someone_else = {(at(18, 0), Kind.START, "Zeynep Kaya")}

    assert due_slot(TIMETABLE, at(18, 0, 20), fired) is None
    assert due_slot(TIMETABLE, at(18, 1), fired) == MONDAY_SIX_OH_ONE
    assert due_slot(TIMETABLE, at(18, 0, days_later=7), fired) == MONDAY_SIX
    assert due_slot(TIMETABLE, at(18, 0, 20), ended) == MONDAY_SIX
    assert due_slot(TIMETABLE, at(18, 0, 20), someone_else) == MONDAY_SIX


def test_slot_whose_parts_partly_fired_is_due_with_only_the_rest() -> None:
    both = Slot(0, SIX_PM, (ZEYNEP_ENDS, AYSE_STARTS))
    end_fired = {(at(18, 0), Kind.END, "Zeynep Kaya")}
    all_fired = {*end_fired, (at(18, 0), Kind.START, "Ayşe Yılmaz")}

    assert untaken(MONDAY, both, ()) == both
    assert untaken(MONDAY, both, end_fired) == Slot(0, SIX_PM, (AYSE_STARTS,))
    assert untaken(MONDAY, both, all_fired) is None
    assert due_slot(Timetable((both,)), at(18, 0, 10), end_fired) == Slot(
        0, SIX_PM, (AYSE_STARTS,)
    )


def test_skipped_part_is_not_due_while_the_rest_of_its_slot_is() -> None:
    partly = Timetable((Slot(0, SIX_PM, (skipped(ZEYNEP_ENDS), AYSE_STARTS)),))
    wholly = Timetable((Slot(0, SIX_PM, (skipped(ZEYNEP_ENDS), skipped(AYSE_STARTS))),))

    assert due_slot(partly, at(18, 0), ()) == Slot(0, SIX_PM, (AYSE_STARTS,))
    assert due_slot(wholly, at(18, 0), ()) is None


def test_slots_that_came_and_went_between_two_looks_are_missed() -> None:
    assert missed_slots(TIMETABLE, at(17, 59, 30), at(18, 2, 10)) == [
        (MONDAY, MONDAY_SIX),
        (MONDAY, MONDAY_SIX_OH_ONE),
    ]
    assert missed_slots(TIMETABLE, at(18, 0, 30), at(9, 0, days_later=2)) == [
        (MONDAY, MONDAY_SIX_OH_ONE),
        (TUESDAY, TUESDAY_SIX),
    ]
    assert missed_slots(TIMETABLE, at(18, 5), at(17, 0)) == []


def test_slot_of_the_minute_of_the_first_look_is_not_missed() -> None:
    assert missed_slots(TIMETABLE, at(18, 0, 5), at(18, 0, 59)) == []
    assert missed_slots(TIMETABLE, at(18, 0, 59), at(18, 2)) == [
        (MONDAY, MONDAY_SIX_OH_ONE)
    ]


def test_slot_of_the_running_minute_is_missed_only_when_it_is_too_late_for_it() -> None:
    assert missed_slots(TIMETABLE, at(17, 0), at(18, 0, 30)) == []
    assert missed_slots(TIMETABLE, at(17, 0), at(18, 0, 31)) == [(MONDAY, MONDAY_SIX)]


def test_scheduler_reports_a_slot_once_within_its_minute() -> None:
    clock = FakeClock(at(17, 59, 59))
    scheduler, due, missed = make_scheduler(clock)

    for second in range(60):
        clock.now = at(18, 0, second)
        scheduler.check()

    assert (due, missed) == ([MONDAY_SIX], [])


def test_slot_whose_minute_began_before_the_scheduler_is_not_reported() -> None:
    clock = FakeClock(at(18, 0, 3))
    scheduler, due, missed = make_scheduler(clock)

    for second in range(4, 60):
        clock.now = at(18, 0, second)
        scheduler.check()

    assert (due, missed) == ([], [])

    clock.now = at(18, 1)
    scheduler.check()
    assert due == [MONDAY_SIX_OH_ONE]


def test_scheduler_reports_the_slots_it_slept_through() -> None:
    clock = FakeClock(at(17, 59, 30))
    scheduler, due, missed = make_scheduler(clock)

    clock.now = at(18, 30)
    scheduler.check()

    assert due == []
    assert missed == [(MONDAY, MONDAY_SIX), (MONDAY, MONDAY_SIX_OH_ONE)]


def test_slot_is_announced_when_the_computer_wakes_just_in_time_for_it() -> None:
    clock = FakeClock(at(17, 0))
    scheduler, due, missed = make_scheduler(clock)

    clock.now = at(18, 1, 20)
    scheduler.check()

    assert due == [MONDAY_SIX_OH_ONE]
    assert missed == [(MONDAY, MONDAY_SIX)]


def test_slot_is_missed_when_the_computer_wakes_too_late_in_its_minute() -> None:
    clock = FakeClock(at(17, 0))
    scheduler, due, missed = make_scheduler(clock)

    clock.now = at(18, 1, 40)
    scheduler.check()
    clock.now = at(18, 1, 41)
    scheduler.check()

    assert due == []
    assert missed == [(MONDAY, MONDAY_SIX), (MONDAY, MONDAY_SIX_OH_ONE)]


def test_slot_is_missed_when_a_short_sleep_makes_it_too_late() -> None:
    clock = FakeClock(at(17, 59, 50))
    scheduler, due, missed = make_scheduler(clock)

    clock.now = at(18, 0, 35)
    scheduler.check()

    assert (due, missed) == ([], [(MONDAY, MONDAY_SIX)])


def test_slot_announced_before_a_sleep_is_not_reported_as_missed() -> None:
    clock = FakeClock(at(17, 59, 59))
    scheduler, due, missed = make_scheduler(clock)

    clock.now = at(18, 0, 1)
    scheduler.check()
    clock.now = at(20, 0)
    scheduler.check()

    assert due == [MONDAY_SIX]
    assert missed == [(MONDAY, MONDAY_SIX_OH_ONE)]


def test_scheduler_does_not_repeat_a_slot_when_the_clock_is_set_back() -> None:
    clock = FakeClock(at(17, 59, 59))
    scheduler, due, _ = make_scheduler(clock)

    for moment in (at(18, 0), at(18, 5), at(18, 0, 10)):
        clock.now = moment
        scheduler.check()

    assert due == [MONDAY_SIX]


def test_scheduler_starts_afresh_on_a_new_date() -> None:
    clock = FakeClock(at(17, 59, 59))
    scheduler, due, _ = make_scheduler(clock)

    for moment in (at(18, 0), at(18, 0, days_later=1), at(18, 0, days_later=7)):
        clock.now = moment
        scheduler.check()

    assert due == [MONDAY_SIX, TUESDAY_SIX, MONDAY_SIX]


def test_only_the_last_week_of_a_long_gap_is_reported_as_missed() -> None:
    clock = FakeClock(at(12, 0))
    scheduler, _, missed = make_scheduler(clock)

    clock.now = at(12, 0, days_later=365)
    scheduler.check()

    assert len(missed) == len(TIMETABLE.slots)
    assert min(date for date, _ in missed) > at(12, 0, days_later=357).date()


def test_replacing_the_timetable_does_not_repeat_the_current_slot() -> None:
    clock = FakeClock(at(17, 59, 59))
    scheduler, due, _ = make_scheduler(clock)
    clock.now = at(18, 0, 5)
    scheduler.check()

    reworded = dataclasses.replace(AYSE_STARTS, text="Ayşe Yılmaz, dersiniz başlıyor.")
    scheduler.set_timetable(Timetable((Slot(0, SIX_PM, (reworded,)),)))
    clock.now = at(18, 0, 20)
    scheduler.check()

    assert due == [MONDAY_SIX]


def test_slot_added_early_in_the_running_minute_is_reported() -> None:
    clock = FakeClock(at(12, 29, 59))
    scheduler, due, _ = make_scheduler(clock)
    clock.now = at(12, 30, 5)
    scheduler.check()

    new = Part(Kind.CUSTOM, "Yeni duyuru", custom_text="Yeni duyuru")
    added = Slot(0, datetime.time(12, 30), (new,))
    scheduler.set_timetable(Timetable((added, *TIMETABLE.slots)))
    clock.now = at(12, 30, 6)
    scheduler.check()

    assert due == [added]


def test_slot_added_late_in_the_running_minute_is_not_reported() -> None:
    clock = FakeClock(at(12, 29, 59))
    scheduler, due, missed = make_scheduler(clock)
    clock.now = at(12, 30, 40)
    scheduler.check()

    new = Part(Kind.CUSTOM, "Yeni duyuru", custom_text="Yeni duyuru")
    added = Slot(0, datetime.time(12, 30), (new,))
    scheduler.set_timetable(Timetable((added, *TIMETABLE.slots)))
    clock.now = at(12, 30, 41)
    scheduler.check()

    assert (due, missed) == ([], [])


def test_part_added_to_a_minute_that_already_fired_is_handed_over_alone() -> None:
    clock = FakeClock(at(18, 54, 59))
    scheduler, due, missed = make_scheduler(clock, planned(ends=False))
    clock.now = at(18, 55)
    scheduler.check()

    scheduler.set_timetable(planned())
    for moment in every_second(at(18, 55, 10), at(18, 55, 59)):
        clock.now = moment
        scheduler.check()

    assert said(due) == [
        (FIVE_TO_SEVEN, (ZEYNEP_COMES,)),
        (FIVE_TO_SEVEN, (AYSE_GOES,)),
    ]
    assert missed == []


def test_skipped_part_is_not_announced_while_the_rest_of_its_slot_is() -> None:
    timetable = planned(
        Change(MONDAY, at(18, 55), Kind.END, "Ayşe Yılmaz", skipped=True),
        Change(MONDAY, at(18, 56), Kind.CUSTOM, DRINK, skipped=True),
    )
    clock = FakeClock(at(18, 54, 59))
    scheduler, due, missed = make_scheduler(clock, timetable)

    for moment in every_second(at(18, 55), at(18, 57)):
        clock.now = moment
        scheduler.check()

    assert (said(due), missed) == ([(FIVE_TO_SEVEN, (ZEYNEP_COMES,))], [])


def test_skipped_part_is_not_reported_missed_while_the_rest_of_its_slot_is() -> None:
    timetable = planned(
        Change(MONDAY, at(18, 55), Kind.END, "Ayşe Yılmaz", skipped=True),
        Change(MONDAY, at(18, 56), Kind.CUSTOM, DRINK, skipped=True),
    )
    clock = FakeClock(at(18, 54, 30))
    scheduler, due, missed = make_scheduler(clock, timetable)

    clock.now = at(19, 25)
    scheduler.check()

    assert due == []
    assert said(slot for _, slot in missed) == [(FIVE_TO_SEVEN, (ZEYNEP_COMES,))]
    assert [date for date, _ in missed] == [MONDAY]


def test_part_unskipped_early_in_its_minute_is_still_announced() -> None:
    clock = FakeClock(at(18, 54, 59))
    scheduler, due, missed = make_scheduler(
        clock,
        planned(Change(MONDAY, at(18, 55), Kind.START, "Zeynep Kaya", skipped=True)),
    )
    clock.now = at(18, 55)
    scheduler.check()
    assert said(due) == [(FIVE_TO_SEVEN, (AYSE_GOES,))]

    scheduler.set_timetable(planned())
    for moment in (at(18, 55, 29), at(18, 55, 30)):
        clock.now = moment
        scheduler.check()

    assert said(due) == [
        (FIVE_TO_SEVEN, (AYSE_GOES,)),
        (FIVE_TO_SEVEN, (ZEYNEP_COMES,)),
    ]
    assert missed == []


@pytest.mark.parametrize("start_delay", [0, 1])
def test_part_moved_by_a_day_change_is_announced_at_its_new_minute_only(
    start_delay: int,
) -> None:
    moved = Change(
        MONDAY, at(18, 55), Kind.START, "Zeynep Kaya", time=datetime.time(18, 58)
    )
    clock = FakeClock(at(18, 54, 59))
    scheduler, due, missed = make_scheduler(
        clock, planned(moved, start_delay=start_delay)
    )

    for minute in range(55, 60):
        for second in (0, 1, 30):
            clock.now = at(18, minute, second)
            scheduler.check()

    assert said(due) == [
        (FIVE_TO_SEVEN, (AYSE_GOES,)),
        (datetime.time(18, 56), (DRINK,)),
        (datetime.time(18, 58), (ZEYNEP_COMES,)),  # the delay is not added to it
    ]
    assert missed == []


def test_first_look_leaves_out_exactly_the_parts_of_its_minute() -> None:
    # Restarted at 18:55:05 with a delay of a minute for the ends: Zeynep's start
    # may have been announced already, Ayşe's end, set for 18:55 too, is still to
    # come.
    clock = FakeClock(at(18, 55, 5))
    scheduler, due, missed = make_scheduler(clock, planned(end_delay=1))

    for moment in every_second(at(18, 55, 6), at(18, 56, 59)):
        clock.now = moment
        scheduler.check()

    assert (said(due), missed) == ([(datetime.time(18, 56), (AYSE_GOES, DRINK))], [])


def test_first_look_does_not_leave_out_a_part_moved_away_from_its_minute() -> None:
    moved = Change(
        MONDAY, at(18, 55), Kind.START, "Zeynep Kaya", time=datetime.time(18, 58)
    )
    clock = FakeClock(at(18, 56, 5))
    scheduler, due, missed = make_scheduler(
        clock, planned(moved, start_delay=1, end_delay=2)
    )

    for minute in range(57, 60):
        clock.now = at(18, minute)
        scheduler.check()

    assert said(due) == [
        (datetime.time(18, 57), (AYSE_GOES,)),
        (datetime.time(18, 58), (ZEYNEP_COMES,)),
    ]
    assert missed == []


@pytest.mark.parametrize(
    ("start_delay", "end_delay", "announced"),
    [
        (0, 0, [(FIVE_TO_SEVEN, (AYSE_GOES,)), (datetime.time(18, 56), (DRINK,))]),
        (2, 1, [(datetime.time(18, 56), (AYSE_GOES, DRINK))]),
        (
            3,
            3,
            [(datetime.time(18, 56), (DRINK,)), (datetime.time(18, 58), (AYSE_GOES,))],
        ),
    ],
)
def test_delay_changed_after_the_first_look_does_not_repeat_what_it_left_out(
    start_delay: int,
    end_delay: int,
    announced: list[tuple[datetime.time, tuple[str, ...]]],
) -> None:
    clock = FakeClock(at(18, 55, 5))
    scheduler, due, missed = make_scheduler(clock, planned(end_delay=1))

    scheduler.set_timetable(planned(start_delay=start_delay, end_delay=end_delay))
    clock.now = at(18, 55, 10)
    scheduler.check()
    for minute in range(56, 60):
        for second in (0, 1, 30):
            clock.now = at(18, minute, second)
            scheduler.check()

    assert (said(due), missed) == (announced, [])


def test_part_moved_a_day_past_its_moment_is_announced_once() -> None:
    sunday_night = datetime.datetime.combine(SUNDAY, datetime.time(23, 55))
    # With the delay, Sunday's 23:55 is read at 00:05 on Monday.
    timetable = build_timetable(
        [AthleteEntry("Zeynep Kaya", 6, sunday_night.time())],
        [],
        "{ad_soyad}, hoş geldiniz.",
        start_delay=10,
        changes=[
            Change(
                MONDAY,
                sunday_night,
                Kind.START,
                "Zeynep Kaya",
                time=datetime.time(23, 59),
            )
        ],
    )
    clock = FakeClock(at(23, 58, 59))
    scheduler, due, _ = make_scheduler(clock, timetable)

    for moment in every_second(at(23, 59), at(23, 59, 30)):
        clock.now = moment
        scheduler.check()

    assert said(due) == [(datetime.time(23, 59), (ZEYNEP_COMES,))]


def two_at_seven(
    *changes: Change, names: Sequence[str] = ("Zeynep Kaya", "Mehmet Demir")
) -> Timetable:
    return build_timetable(
        [AthleteEntry(name, 0, SEVEN_PM) for name in names],
        [],
        "{ad_soyad}, hoş geldiniz.",
        changes=changes,
    )


def test_athletes_of_a_minute_are_handed_over_together_in_schedule_order() -> None:
    clock = FakeClock(at(18, 59, 59))
    scheduler, due, missed = make_scheduler(clock, two_at_seven())

    for moment in every_second(at(19, 0), at(19, 1)):
        clock.now = moment
        scheduler.check()

    assert (said(due), missed) == ([(SEVEN_PM, (ZEYNEP_COMES, MEHMET_COMES))], [])
    assert [part.name for part in due[0].parts] == ["Zeynep Kaya", "Mehmet Demir"]


def test_athlete_whose_part_fired_leaves_the_other_of_the_minute_due() -> None:
    timetable = two_at_seven()
    slot = timetable.slot_at(MONDAY, SEVEN_PM)
    assert slot is not None
    _, mehmet = slot.parts
    zeynep_fired = {(at(19, 0), Kind.START, "Zeynep Kaya")}
    mehmet_alone = Slot(0, SEVEN_PM, (mehmet,))

    assert untaken(MONDAY, slot, zeynep_fired) == mehmet_alone
    assert due_slot(timetable, at(19, 0, 5), zeynep_fired) == mehmet_alone


def test_athlete_added_to_a_minute_that_already_fired_is_handed_over_alone() -> None:
    clock = FakeClock(at(18, 59, 59))
    scheduler, due, missed = make_scheduler(clock, two_at_seven(names=["Zeynep Kaya"]))
    clock.now = at(19, 0)
    scheduler.check()

    scheduler.set_timetable(two_at_seven())
    for moment in every_second(at(19, 0, 10), at(19, 0, 59)):
        clock.now = moment
        scheduler.check()

    assert said(due) == [(SEVEN_PM, (ZEYNEP_COMES,)), (SEVEN_PM, (MEHMET_COMES,))]
    assert missed == []


def test_skipping_one_athlete_leaves_the_other_of_the_minute_announced() -> None:
    timetable = two_at_seven(
        Change(MONDAY, at(19, 0), Kind.START, "Zeynep Kaya", skipped=True)
    )
    clock = FakeClock(at(18, 59, 59))
    scheduler, due, missed = make_scheduler(clock, timetable)

    for moment in every_second(at(19, 0), at(19, 1)):
        clock.now = moment
        scheduler.check()

    assert (said(due), missed) == ([(SEVEN_PM, (MEHMET_COMES,))], [])


def test_skipped_athlete_is_not_reported_missed_while_the_other_one_is() -> None:
    timetable = two_at_seven(
        Change(MONDAY, at(19, 0), Kind.START, "Zeynep Kaya", skipped=True)
    )
    clock = FakeClock(at(18, 59, 30))
    scheduler, due, missed = make_scheduler(clock, timetable)

    clock.now = at(19, 30)
    scheduler.check()

    assert due == []
    assert said(slot for _, slot in missed) == [(SEVEN_PM, (MEHMET_COMES,))]


def test_athlete_unskipped_early_in_the_minute_is_announced_alone() -> None:
    clock = FakeClock(at(18, 59, 59))
    scheduler, due, missed = make_scheduler(
        clock,
        two_at_seven(
            Change(MONDAY, at(19, 0), Kind.START, "Zeynep Kaya", skipped=True)
        ),
    )
    clock.now = at(19, 0)
    scheduler.check()

    scheduler.set_timetable(two_at_seven())
    for moment in every_second(at(19, 0, 10), at(19, 0, 59)):
        clock.now = moment
        scheduler.check()

    assert said(due) == [(SEVEN_PM, (MEHMET_COMES,)), (SEVEN_PM, (ZEYNEP_COMES,))]
    assert missed == []


def test_editing_one_athlete_leaves_the_other_of_the_minute_in_place() -> None:
    edited = Change(
        MONDAY,
        at(19, 0),
        Kind.START,
        "Zeynep Kaya",
        time=datetime.time(19, 10),
        text="{ad_soyad}, dersiniz on dakika gecikmeli başlıyor.",
    )
    clock = FakeClock(at(18, 59, 59))
    scheduler, due, missed = make_scheduler(clock, two_at_seven(edited))

    for minute in range(15):
        for second in (0, 1, 30):
            clock.now = at(19, minute, second)
            scheduler.check()

    assert said(due) == [
        (SEVEN_PM, (MEHMET_COMES,)),
        (
            datetime.time(19, 10),
            ("Zeynep Kaya, dersiniz on dakika gecikmeli başlıyor.",),
        ),
    ]
    assert missed == []


def test_new_sentence_for_an_athlete_already_announced_does_not_repeat_it() -> None:
    clock = FakeClock(at(18, 59, 59))
    scheduler, due, missed = make_scheduler(clock, two_at_seven())
    clock.now = at(19, 0, 1)
    scheduler.check()

    scheduler.set_timetable(
        two_at_seven(
            Change(MONDAY, at(19, 0), Kind.START, "Zeynep Kaya", text="Yeni cümle.")
        )
    )
    for moment in every_second(at(19, 0, 2), at(19, 1)):
        clock.now = moment
        scheduler.check()

    assert (said(due), missed) == ([(SEVEN_PM, (ZEYNEP_COMES, MEHMET_COMES))], [])


def reminders_at_seven(*changes: Change) -> Timetable:
    return build_timetable(
        [],
        [
            CustomAnnouncement(text, SEVEN_PM, frozenset({0}))
            for text in (DRINK, CLOSING)
        ],
        "{ad_soyad}, hoş geldiniz.",
        changes=changes,
    )


def test_custom_announcement_that_fired_leaves_the_other_of_the_minute_due() -> None:
    timetable = reminders_at_seven()
    slot = timetable.slot_at(MONDAY, SEVEN_PM)
    assert slot is not None
    _, closing = slot.parts
    drink_fired = {(at(19, 0), Kind.CUSTOM, DRINK)}
    closing_alone = Slot(0, SEVEN_PM, (closing,))

    assert untaken(MONDAY, slot, drink_fired) == closing_alone
    assert due_slot(timetable, at(19, 0, 5), drink_fired) == closing_alone


def test_reworded_custom_announcement_that_was_read_is_not_repeated() -> None:
    clock = FakeClock(at(18, 59, 59))
    scheduler, due, missed = make_scheduler(clock, reminders_at_seven())
    clock.now = at(19, 0, 1)
    scheduler.check()

    scheduler.set_timetable(
        reminders_at_seven(
            Change(MONDAY, at(19, 0), Kind.CUSTOM, DRINK, text="Yeni cümle.")
        )
    )
    for moment in every_second(at(19, 0, 2), at(19, 1)):
        clock.now = moment
        scheduler.check()

    assert (said(due), missed) == ([(SEVEN_PM, (DRINK, CLOSING))], [])


def test_background_thread_reports_a_due_slot_with_its_date_and_stops() -> None:
    reported: list[tuple[datetime.date, Slot]] = []
    done = threading.Event()

    def on_due(date: datetime.date, slot: Slot) -> None:
        reported.append((date, slot))
        done.set()

    clock = FakeClock(at(17, 59, 59, days_later=1))
    scheduler = Scheduler(TIMETABLE, on_due, ignore, clock, interval=0.01)
    scheduler.start()
    try:
        clock.now = at(18, 0, days_later=1)
        assert done.wait(timeout=5)
    finally:
        scheduler.stop()

    assert reported == [(TUESDAY, TUESDAY_SIX)]
    assert all(thread.name != "scheduler" for thread in threading.enumerate())


def test_scheduler_begins_when_it_is_started() -> None:
    reported: list[Slot] = []
    done = threading.Event()

    def on_due(date: datetime.date, slot: Slot) -> None:
        reported.append(slot)
        done.set()

    clock = FakeClock(at(18, 0, 2))
    scheduler = Scheduler(TIMETABLE, on_due, ignore, clock, interval=0.01)
    try:
        assert scheduler.start() == at(18, 0, 2)
        assert not done.wait(timeout=0.1)
        clock.now = at(18, 1)
        assert done.wait(timeout=5)
    finally:
        scheduler.stop()

    assert reported == [MONDAY_SIX_OH_ONE]


def test_background_thread_survives_a_failing_callback() -> None:
    clock = FakeClock(at(17, 59, 59))
    reported: list[Slot] = []
    failed = threading.Event()
    done = threading.Event()

    def on_due(date: datetime.date, slot: Slot) -> None:
        if not failed.is_set():
            failed.set()
            raise RuntimeError("the first hand-over fails")
        reported.append(slot)
        done.set()

    scheduler = Scheduler(TIMETABLE, on_due, ignore, clock, interval=0.01)
    scheduler.start()
    try:
        clock.now = at(18, 0)
        assert failed.wait(timeout=5)
        clock.now = at(18, 1)
        assert done.wait(timeout=5)
    finally:
        scheduler.stop()

    assert reported == [MONDAY_SIX_OH_ONE]


def test_stopping_a_scheduler_that_never_started_is_harmless() -> None:
    scheduler, due, _ = make_scheduler(FakeClock(at(18, 0)))

    scheduler.stop()

    assert due == []


def delayed_by(minutes: int) -> Timetable:
    return build_timetable(
        [
            AthleteEntry("Ayşe Yılmaz", 0, SIX_PM),
            AthleteEntry("Zeynep Kaya", 0, datetime.time(23, 55)),
        ],
        [],
        "{ad_soyad}",
        start_delay=minutes,
    )


def test_slots_that_a_shorter_delay_puts_behind_the_clock_are_brought_forward() -> None:
    five = {Kind.START: datetime.timedelta(minutes=5)}
    ten = {Kind.START: datetime.timedelta(minutes=10)}
    longer = {Kind.START: datetime.timedelta(minutes=-5)}
    ayse, zeynep = delayed_by(0).slots

    assert brought_forward(delayed_by(0), at(18, 2), five) == [(MONDAY, ayse)]
    assert brought_forward(delayed_by(0), at(18, 0), five) == [(MONDAY, ayse)]
    assert brought_forward(delayed_by(0), at(17, 59, 59), five) == []
    assert brought_forward(delayed_by(0), at(18, 5), five) == []  # it was over
    assert brought_forward(delayed_by(0), at(0, 1, days_later=1), ten) == [
        (MONDAY, zeynep)
    ]
    assert brought_forward(delayed_by(5), at(18, 2), longer) == []


def test_shorter_delay_brings_forward_only_the_parts_of_its_kind() -> None:
    five = datetime.timedelta(minutes=5)
    both = planned().slot_at(MONDAY, FIVE_TO_SEVEN)
    assert both is not None
    goes, comes = both.parts
    ayse_goes = Slot(0, FIVE_TO_SEVEN, (goes,))
    zeynep_comes = Slot(0, FIVE_TO_SEVEN, (comes,))
    before = at(18, 57)

    assert brought_forward(planned(), before, {Kind.START: five}) == [
        (MONDAY, zeynep_comes)
    ]
    assert brought_forward(planned(), before, {Kind.END: five}) == [(MONDAY, ayse_goes)]
    assert brought_forward(planned(), before, {Kind.END: five, Kind.START: -five}) == [
        (MONDAY, ayse_goes)
    ]
    assert brought_forward(planned(), before, {Kind.END: five, Kind.START: five}) == [
        (MONDAY, both)
    ]
    assert brought_forward(planned(), before, {}) == []


def test_longer_delay_does_not_repeat_what_was_announced() -> None:
    clock = FakeClock(at(17, 59, 59))
    scheduler, due, missed = make_scheduler(clock, delayed_by(0))
    clock.now = at(18, 0, 1)
    scheduler.check()

    scheduler.set_timetable(delayed_by(10))
    for moment in (at(18, 5), at(18, 10), at(18, 11), at(18, 30)):
        clock.now = moment
        scheduler.check()

    assert ([slot.time for slot in due], missed) == ([SIX_PM], [])


def test_longer_delay_moves_what_is_still_to_come() -> None:
    clock = FakeClock(at(17, 58))
    scheduler, due, missed = make_scheduler(clock, delayed_by(0))

    scheduler.set_timetable(delayed_by(2))
    for moment in (at(17, 59), at(18, 0), at(18, 1), at(18, 2), at(18, 3)):
        clock.now = moment
        scheduler.check()

    assert ([slot.time for slot in due], missed) == ([datetime.time(18, 2)], [])


def test_shorter_delay_announces_at_once_what_it_puts_behind_the_clock() -> None:
    clock = FakeClock(at(17, 59))
    scheduler, due, missed = make_scheduler(clock, delayed_by(5))
    clock.now = at(18, 2, 40)
    scheduler.check()
    assert due == []

    scheduler.set_timetable(delayed_by(0))
    for moment in (at(18, 2, 41), at(18, 2, 42), at(18, 3), at(18, 5)):
        clock.now = moment
        scheduler.check()

    assert (due, missed) == ([delayed_by(0).slots[0]], [])


def test_shorter_delay_does_not_repeat_what_was_announced() -> None:
    clock = FakeClock(at(17, 59))
    scheduler, due, missed = make_scheduler(clock, delayed_by(5))
    clock.now = at(18, 5)
    scheduler.check()
    clock.now = at(18, 6)
    scheduler.check()

    scheduler.set_timetable(delayed_by(3))
    clock.now = at(18, 6, 1)
    scheduler.check()

    assert ([slot.time for slot in due], missed) == ([datetime.time(18, 5)], [])


def test_delay_past_midnight_keeps_a_slot_from_being_announced_twice() -> None:
    clock = FakeClock(at(23, 54))
    scheduler, due, missed = make_scheduler(clock, delayed_by(0))
    clock.now = at(23, 55)
    scheduler.check()

    scheduler.set_timetable(delayed_by(10))
    for moment in (at(23, 59), at(0, 5, days_later=1), at(0, 6, days_later=1)):
        clock.now = moment
        scheduler.check()

    assert ([slot.time for slot in due], missed) == ([datetime.time(23, 55)], [])


@pytest.mark.parametrize(
    ("start_delay", "end_delay", "brought"),
    [(5, 0, ZEYNEP_COMES), (0, 5, AYSE_GOES)],
)
def test_scheduler_brings_forward_only_the_parts_whose_delay_got_shorter(
    start_delay: int, end_delay: int, brought: str
) -> None:
    clock = FakeClock(at(18, 57, 10))
    scheduler, due, missed = make_scheduler(
        clock, planned(start_delay=start_delay, end_delay=end_delay)
    )

    scheduler.set_timetable(planned())
    for moment in every_second(at(18, 57, 40), at(19, 1)):
        clock.now = moment
        scheduler.check()

    assert (said(due), missed) == ([(FIVE_TO_SEVEN, (brought,))], [])


def test_delays_changed_in_opposite_ways_neither_drop_nor_repeat_a_part() -> None:
    clock = FakeClock(at(18, 54, 59))
    scheduler, due, missed = make_scheduler(clock, planned(start_delay=5))
    for moment in every_second(at(18, 55), at(18, 57, 39)):
        clock.now = moment
        scheduler.check()

    scheduler.set_timetable(planned(end_delay=5))
    for moment in every_second(at(18, 57, 40), at(19, 1)):
        clock.now = moment
        scheduler.check()

    assert said(due) == [
        (FIVE_TO_SEVEN, (AYSE_GOES,)),
        (datetime.time(18, 56), (DRINK,)),
        (FIVE_TO_SEVEN, (ZEYNEP_COMES,)),
    ]
    assert missed == []


def moved_to(time: datetime.time, delay: int) -> Timetable:
    return build_timetable(
        [
            AthleteEntry("Ayşe Yılmaz", 0, SIX_PM),
            AthleteEntry("Zeynep Kaya", 0, datetime.time(18, 26)),
        ],
        [],
        "{ad_soyad}, hoş geldiniz.",
        start_delay=delay,
        changes=[Change(MONDAY, at(18, 0), Kind.START, "Ayşe Yılmaz", time=time)],
    )


def test_brought_forward_holds_only_the_parts_that_the_delay_moves() -> None:
    three = {Kind.START: datetime.timedelta(minutes=3)}
    zeynep = dataclasses.replace(ZEYNEP_STARTS, delay=2)
    shared = moved_to(datetime.time(18, 28), delay=2)  # where Zeynep's start is
    apart = moved_to(datetime.time(18, 27), delay=2)

    delays = [part.delay for slot in shared.slots_on(MONDAY) for part in slot.parts]
    assert delays == [28, 2]  # Ayşe's, moved there, and Zeynep's
    assert brought_forward(shared, at(18, 29, 10), three) == [
        (MONDAY, Slot(0, datetime.time(18, 28), (zeynep,)))
    ]
    assert brought_forward(apart, at(18, 29, 10), three) == [
        (MONDAY, Slot(0, datetime.time(18, 28), (zeynep,)))
    ]
    assert brought_forward(apart, at(18, 27, 30), three) == []


def test_shorter_delay_leaves_a_part_moved_to_a_time_of_its_own_there() -> None:
    # Restarted at 18:29:10 with a delay of five minutes, after Ayşe's start,
    # which the operator moved to 18:28; Zeynep's is still to come at 18:31.
    half_past = datetime.time(18, 28)
    clock = FakeClock(at(18, 29, 10))
    scheduler, due, missed = make_scheduler(clock, moved_to(half_past, delay=5))
    for moment in every_second(at(18, 29, 11), at(18, 30, 4)):
        clock.now = moment
        scheduler.check()
    assert due == []

    scheduler.set_timetable(moved_to(half_past, delay=0))
    for moment in every_second(at(18, 30, 5), at(18, 35)):
        clock.now = moment
        scheduler.check()

    assert said(due) == [(datetime.time(18, 26), (ZEYNEP_COMES,))]
    assert missed == []


def test_part_moved_to_where_the_new_delay_puts_it_is_not_brought_forward() -> None:
    # With a delay of five minutes, the operator moved Ayşe's start back to six;
    # the app was closed then and restarted at 18:01:10.
    clock = FakeClock(at(18, 1, 10))
    scheduler, due, missed = make_scheduler(clock, moved_to(SIX_PM, delay=5))

    scheduler.set_timetable(moved_to(SIX_PM, delay=0))
    for moment in every_second(at(18, 2, 5), at(18, 5)):
        clock.now = moment
        scheduler.check()

    assert (said(due), missed) == ([], [])
