"""Tests for deciding when a slot is due."""

import datetime
import threading

from announcer.scheduler import Scheduler, due_slot
from announcer.timetable import Slot, Timetable

MONDAY = datetime.date(2026, 11, 9)
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


class FakeClock:
    """A clock that the test moves by hand."""

    def __init__(self, now: datetime.datetime) -> None:
        self.now = now

    def __call__(self) -> datetime.datetime:
        return self.now


def make_scheduler(
    clock: FakeClock, timetable: Timetable = TIMETABLE
) -> tuple[Scheduler, list[Slot]]:
    """Return a scheduler that is driven by hand and the slots it reports."""
    reported: list[Slot] = []
    return Scheduler(timetable, reported.append, clock), reported


def test_the_test_monday_is_a_monday() -> None:
    assert MONDAY.weekday() == 0


def test_slot_is_due_at_any_second_of_its_minute() -> None:
    assert due_slot(TIMETABLE, at(18, 0, 0), ()) == MONDAY_SIX
    assert due_slot(TIMETABLE, at(18, 0, 31), ()) == MONDAY_SIX
    assert due_slot(TIMETABLE, at(18, 0, 59), ()) == MONDAY_SIX


def test_nothing_is_due_outside_a_slot_minute() -> None:
    assert due_slot(TIMETABLE, at(17, 59, 59), ()) is None
    assert due_slot(TIMETABLE, at(18, 2), ()) is None
    assert due_slot(TIMETABLE, at(6, 0), ()) is None


def test_slot_is_due_only_on_its_weekday() -> None:
    assert due_slot(TIMETABLE, at(18, 0, days_later=1), ()) == TUESDAY_SIX
    assert due_slot(TIMETABLE, at(18, 1, days_later=1), ()) is None
    assert due_slot(TIMETABLE, at(18, 0, days_later=2), ()) is None


def test_slot_that_already_fired_is_not_due_again() -> None:
    assert due_slot(TIMETABLE, at(18, 0, 30), {SIX_PM}) is None
    assert due_slot(TIMETABLE, at(18, 1), {SIX_PM}) == MONDAY_SIX_OH_ONE


def test_nothing_is_due_in_an_empty_timetable() -> None:
    assert due_slot(Timetable(), at(18, 0), ()) is None


def test_scheduler_reports_a_slot_once_within_its_minute() -> None:
    clock = FakeClock(at(17, 59, 59))
    scheduler, reported = make_scheduler(clock)

    scheduler.check()
    assert reported == []

    for second in range(60):
        clock.now = at(18, 0, second)
        scheduler.check()
    assert reported == [MONDAY_SIX]


def test_scheduler_reports_consecutive_slots() -> None:
    clock = FakeClock(at(18, 0))
    scheduler, reported = make_scheduler(clock)

    scheduler.check()
    clock.now = at(18, 1)
    scheduler.check()
    clock.now = at(18, 2)
    scheduler.check()

    assert reported == [MONDAY_SIX, MONDAY_SIX_OH_ONE]


def test_scheduler_reports_a_slot_when_started_late_in_its_minute() -> None:
    clock = FakeClock(at(18, 0, 58))
    scheduler, reported = make_scheduler(clock)

    scheduler.check()

    assert reported == [MONDAY_SIX]


def test_scheduler_does_not_catch_up_on_a_missed_slot() -> None:
    clock = FakeClock(at(17, 59))
    scheduler, reported = make_scheduler(clock)

    scheduler.check()
    clock.now = at(18, 2)  # the computer slept through 18:00 and 18:01
    scheduler.check()

    assert reported == []


def test_scheduler_does_not_repeat_a_slot_when_the_clock_is_set_back() -> None:
    clock = FakeClock(at(18, 0))
    scheduler, reported = make_scheduler(clock)

    scheduler.check()
    clock.now = at(18, 5)
    scheduler.check()
    clock.now = at(18, 0, 10)
    scheduler.check()

    assert reported == [MONDAY_SIX]


def test_scheduler_reports_the_same_slot_again_a_week_later() -> None:
    clock = FakeClock(at(18, 0))
    scheduler, reported = make_scheduler(clock)

    scheduler.check()
    clock.now = at(18, 0, days_later=7)
    scheduler.check()

    assert reported == [MONDAY_SIX, MONDAY_SIX]


def test_scheduler_forgets_fired_slots_on_a_new_date() -> None:
    clock = FakeClock(at(18, 0))
    scheduler, reported = make_scheduler(clock)

    scheduler.check()
    clock.now = at(18, 0, days_later=1)
    scheduler.check()

    assert reported == [MONDAY_SIX, TUESDAY_SIX]


def test_replacing_the_timetable_does_not_repeat_the_current_slot() -> None:
    clock = FakeClock(at(18, 0, 5))
    scheduler, reported = make_scheduler(clock)
    scheduler.check()

    changed = Slot(0, SIX_PM, ("Ayşe Yılmaz, dersiniz başlıyor.",))
    scheduler.set_timetable(Timetable((changed,)))
    clock.now = at(18, 0, 20)
    scheduler.check()

    assert reported == [MONDAY_SIX]


def test_slot_added_to_the_current_minute_is_reported() -> None:
    clock = FakeClock(at(12, 30, 5))
    scheduler, reported = make_scheduler(clock)
    scheduler.check()

    added = Slot(0, datetime.time(12, 30), ("Yeni duyuru",))
    scheduler.set_timetable(Timetable((added, *TIMETABLE.slots)))
    clock.now = at(12, 30, 6)
    scheduler.check()

    assert reported == [added]


def test_new_timetable_is_used_for_later_slots() -> None:
    clock = FakeClock(at(17, 0))
    scheduler, reported = make_scheduler(clock)

    scheduler.set_timetable(Timetable((MONDAY_SIX_OH_ONE,)))
    clock.now = at(18, 0)
    scheduler.check()
    clock.now = at(18, 1)
    scheduler.check()

    assert reported == [MONDAY_SIX_OH_ONE]


def test_background_thread_reports_a_due_slot_and_stops() -> None:
    reported: list[Slot] = []
    done = threading.Event()

    def on_due(slot: Slot) -> None:
        reported.append(slot)
        done.set()

    scheduler = Scheduler(TIMETABLE, on_due, FakeClock(at(18, 0)), interval=0.01)
    scheduler.start()
    try:
        assert done.wait(timeout=5)
    finally:
        scheduler.stop()

    assert reported == [MONDAY_SIX]
    assert all(thread.name != "scheduler" for thread in threading.enumerate())


def test_background_thread_survives_a_failing_callback() -> None:
    clock = FakeClock(at(18, 0))
    reported: list[Slot] = []
    failed = threading.Event()
    done = threading.Event()

    def on_due(slot: Slot) -> None:
        if not failed.is_set():
            failed.set()
            raise RuntimeError("the first hand-over fails")
        reported.append(slot)
        done.set()

    scheduler = Scheduler(TIMETABLE, on_due, clock, interval=0.01)
    scheduler.start()
    try:
        assert failed.wait(timeout=5)
        clock.now = at(18, 1)
        assert done.wait(timeout=5)
    finally:
        scheduler.stop()

    assert reported == [MONDAY_SIX_OH_ONE]


def test_stopping_a_scheduler_that_never_started_is_harmless() -> None:
    scheduler, reported = make_scheduler(FakeClock(at(18, 0)))

    scheduler.stop()

    assert reported == []
