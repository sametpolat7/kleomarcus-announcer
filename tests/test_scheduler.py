"""Tests for deciding when a slot is due."""

import datetime
import threading

from announcer.scheduler import Scheduler, due_slot, missed_slots
from announcer.timetable import Slot, Timetable
from support import FakeClock

MONDAY = datetime.date(2026, 11, 9)
TUESDAY = MONDAY + datetime.timedelta(days=1)
SIX_PM = datetime.time(18, 0)

MONDAY_SIX = Slot(0, SIX_PM, ("Ayşe Yılmaz, hoş geldiniz.",))
MONDAY_SIX_OH_ONE = Slot(0, datetime.time(18, 1), ("Su içmeyi unutmayın.",))
TUESDAY_SIX = Slot(1, SIX_PM, ("Zeynep Kaya, hoş geldiniz.",))
TIMETABLE = Timetable((MONDAY_SIX, MONDAY_SIX_OH_ONE, TUESDAY_SIX))


def at(
    hour: int, minute: int, second: int = 0, *, days_later: int = 0
) -> datetime.datetime:
    """Return a moment on the test Monday, or a number of days after it."""
    day = MONDAY + datetime.timedelta(days=days_later)
    return datetime.datetime.combine(day, datetime.time(hour, minute, second))


def ignore(date: datetime.date, slot: Slot) -> None:
    """Take a reported slot and do nothing with it."""


def make_scheduler(
    clock: FakeClock, timetable: Timetable = TIMETABLE
) -> tuple[Scheduler, list[Slot], list[tuple[datetime.date, Slot]]]:
    """Return a scheduler that has begun at the clock's time and is driven by
    hand from there, the slots it reports as due and those as missed."""
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


def test_the_test_monday_is_a_monday() -> None:
    assert MONDAY.weekday() == 0


def test_slot_is_due_in_the_first_half_of_its_minute() -> None:
    assert due_slot(TIMETABLE, at(18, 0, 0), ()) == MONDAY_SIX
    assert due_slot(TIMETABLE, at(18, 0, 2), ()) == MONDAY_SIX
    assert due_slot(TIMETABLE, at(18, 0, 30), ()) == MONDAY_SIX


def test_slot_is_no_longer_due_when_its_minute_began_too_long_ago() -> None:
    assert due_slot(TIMETABLE, at(18, 0, 31), ()) is None
    assert due_slot(TIMETABLE, at(18, 0, 59), ()) is None


def test_nothing_is_due_outside_a_slot_minute() -> None:
    assert due_slot(TIMETABLE, at(17, 59, 59), ()) is None
    assert due_slot(TIMETABLE, at(18, 2), ()) is None
    assert due_slot(TIMETABLE, at(6, 0), ()) is None


def test_slot_is_due_only_on_its_weekday() -> None:
    assert due_slot(TIMETABLE, at(18, 0, days_later=1), ()) == TUESDAY_SIX
    assert due_slot(TIMETABLE, at(18, 1, days_later=1), ()) is None
    assert due_slot(TIMETABLE, at(18, 0, days_later=2), ()) is None


def test_slot_that_already_fired_is_not_due_again() -> None:
    assert due_slot(TIMETABLE, at(18, 0, 20), {SIX_PM}) is None
    assert due_slot(TIMETABLE, at(18, 1), {SIX_PM}) == MONDAY_SIX_OH_ONE


def test_nothing_is_due_in_an_empty_timetable() -> None:
    assert due_slot(Timetable(), at(18, 0), ()) is None


def test_slots_that_came_and_went_between_two_looks_are_missed() -> None:
    missed = missed_slots(TIMETABLE, at(17, 59, 30), at(18, 2, 10))

    assert missed == [(MONDAY, MONDAY_SIX), (MONDAY, MONDAY_SIX_OH_ONE)]


def test_slot_of_the_minute_of_the_first_look_is_not_missed() -> None:
    # 18:00 was dealt with at the first look.
    assert missed_slots(TIMETABLE, at(18, 0, 5), at(18, 0, 59)) == []
    assert missed_slots(TIMETABLE, at(18, 0, 59), at(18, 2)) == [
        (MONDAY, MONDAY_SIX_OH_ONE)
    ]


def test_slot_of_the_running_minute_is_missed_only_when_it_is_too_late_for_it() -> None:
    assert missed_slots(TIMETABLE, at(17, 0), at(18, 0, 30)) == []
    assert missed_slots(TIMETABLE, at(17, 0), at(18, 0, 31)) == [(MONDAY, MONDAY_SIX)]
    assert missed_slots(TIMETABLE, at(17, 0), at(18, 1, 10)) == [(MONDAY, MONDAY_SIX)]


def test_missed_slots_come_with_their_dates_oldest_first() -> None:
    missed = missed_slots(TIMETABLE, at(18, 0, 30), at(9, 0, days_later=2))

    assert missed == [(MONDAY, MONDAY_SIX_OH_ONE), (TUESDAY, TUESDAY_SIX)]


def test_nothing_is_missed_when_the_clock_goes_back() -> None:
    assert missed_slots(TIMETABLE, at(18, 5), at(17, 0)) == []


def test_scheduler_reports_a_slot_once_within_its_minute() -> None:
    clock = FakeClock(at(17, 59, 59))
    scheduler, due, missed = make_scheduler(clock)

    for second in range(60):
        clock.now = at(18, 0, second)
        scheduler.check()

    assert (due, missed) == ([MONDAY_SIX], [])


def test_scheduler_reports_the_date_with_a_due_slot() -> None:
    dates: list[datetime.date] = []
    clock = FakeClock(at(17, 59, 59, days_later=1))
    scheduler = Scheduler(
        TIMETABLE, lambda date, slot: dates.append(date), ignore, clock
    )
    scheduler.check()

    clock.now = at(18, 0, days_later=1)
    scheduler.check()

    assert dates == [TUESDAY]


def test_scheduler_reports_consecutive_slots() -> None:
    clock = FakeClock(at(17, 59, 59))
    scheduler, due, missed = make_scheduler(clock)

    for second in range(60, 60 + 125):
        clock.now = at(17, 59) + datetime.timedelta(seconds=second)
        scheduler.check()

    assert (due, missed) == ([MONDAY_SIX, MONDAY_SIX_OH_ONE], [])


def test_slot_whose_minute_began_before_the_scheduler_is_not_reported() -> None:
    # It may have been announced by a run of the app that was just closed.
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

    clock.now = at(18, 30)  # the computer slept through 18:00 and 18:01
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

    clock.now = at(18, 0, 35)  # 45 seconds without a look
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


def test_scheduler_reports_the_same_slot_again_a_week_later() -> None:
    clock = FakeClock(at(17, 59, 59))
    scheduler, due, _ = make_scheduler(clock)

    for moment in (at(18, 0), at(18, 0, days_later=7)):
        clock.now = moment
        scheduler.check()

    assert due == [MONDAY_SIX, MONDAY_SIX]


def test_scheduler_forgets_fired_slots_on_a_new_date() -> None:
    clock = FakeClock(at(17, 59, 59))
    scheduler, due, _ = make_scheduler(clock)

    for moment in (at(18, 0), at(18, 0, days_later=1)):
        clock.now = moment
        scheduler.check()

    assert due == [MONDAY_SIX, TUESDAY_SIX]


def test_only_the_last_week_of_a_long_gap_is_reported_as_missed() -> None:
    clock = FakeClock(at(12, 0))
    scheduler, _, missed = make_scheduler(clock)

    clock.now = at(12, 0, days_later=365)  # more likely a corrected clock
    scheduler.check()

    assert len(missed) == len(TIMETABLE.slots)
    assert min(date for date, _ in missed) > at(12, 0, days_later=357).date()


def test_replacing_the_timetable_does_not_repeat_the_current_slot() -> None:
    clock = FakeClock(at(17, 59, 59))
    scheduler, due, _ = make_scheduler(clock)
    clock.now = at(18, 0, 5)
    scheduler.check()

    changed = Slot(0, SIX_PM, ("Ayşe Yılmaz, dersiniz başlıyor.",))
    scheduler.set_timetable(Timetable((changed,)))
    clock.now = at(18, 0, 20)
    scheduler.check()

    assert due == [MONDAY_SIX]


def test_slot_added_early_in_the_running_minute_is_reported() -> None:
    clock = FakeClock(at(12, 29, 59))
    scheduler, due, _ = make_scheduler(clock)
    clock.now = at(12, 30, 5)
    scheduler.check()

    added = Slot(0, datetime.time(12, 30), ("Yeni duyuru",))
    scheduler.set_timetable(Timetable((added, *TIMETABLE.slots)))
    clock.now = at(12, 30, 6)
    scheduler.check()

    assert due == [added]


def test_slot_added_late_in_the_running_minute_is_not_reported() -> None:
    clock = FakeClock(at(12, 29, 59))
    scheduler, due, missed = make_scheduler(clock)
    clock.now = at(12, 30, 40)
    scheduler.check()

    added = Slot(0, datetime.time(12, 30), ("Yeni duyuru",))
    scheduler.set_timetable(Timetable((added, *TIMETABLE.slots)))
    clock.now = at(12, 30, 41)
    scheduler.check()

    assert (due, missed) == ([], [])


def test_new_timetable_is_used_for_later_slots() -> None:
    clock = FakeClock(at(17, 0))
    scheduler, due, _ = make_scheduler(clock)

    scheduler.set_timetable(Timetable((MONDAY_SIX_OH_ONE,)))
    for moment in (at(17, 59, 59), at(18, 0), at(18, 1)):
        clock.now = moment
        scheduler.check()

    assert due == [MONDAY_SIX_OH_ONE]


def test_background_thread_reports_a_due_slot_and_stops() -> None:
    reported: list[Slot] = []
    done = threading.Event()

    def on_due(date: datetime.date, slot: Slot) -> None:
        reported.append(slot)
        done.set()

    clock = FakeClock(at(17, 59, 59))
    scheduler = Scheduler(TIMETABLE, on_due, ignore, clock, interval=0.01)
    scheduler.start()
    try:
        clock.now = at(18, 0)
        assert done.wait(timeout=5)
    finally:
        scheduler.stop()

    assert reported == [MONDAY_SIX]
    assert all(thread.name != "scheduler" for thread in threading.enumerate())


def test_scheduler_begins_when_it_is_started() -> None:
    # The minute that is running at the start is left out, with no wait for
    # the thread's first look.
    reported: list[Slot] = []
    done = threading.Event()

    def on_due(date: datetime.date, slot: Slot) -> None:
        reported.append(slot)
        done.set()

    clock = FakeClock(at(18, 0, 2))
    scheduler = Scheduler(TIMETABLE, on_due, ignore, clock, interval=0.01)
    scheduler.start()
    try:
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
