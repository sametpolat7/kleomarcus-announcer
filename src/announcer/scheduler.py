"""A slot is announced at its time or not at all: by a scheduler that was already
running when the slot's minute began, at most half a minute late, and once per
date. A slot whose minute began while the app was closed is not caught up on.
Neither is one that the app could not announce in time although it ran,
because the computer was asleep; that one is reported as missed, so the app
can show it.
"""

import datetime
import logging
import threading
from collections.abc import Callable, Collection

from announcer.timetable import Slot, Timetable

_log = logging.getLogger(__name__)

_DAY = datetime.timedelta(days=1)
# How long into its minute a slot may still be announced. A second or two is
# normal; more happens when the computer is very busy or wakes from a sleep.
LATE = datetime.timedelta(seconds=30)


def due_slot(
    timetable: Timetable, now: datetime.datetime, fired: Collection[datetime.time]
) -> Slot | None:
    """``fired`` holds the times of the slots already dealt with on ``now``'s date."""
    minute = now.replace(second=0, microsecond=0)
    if minute.time() in fired or now - minute > LATE:
        return None
    return timetable.slot_at(now.weekday(), minute.time())


def missed_slots(
    timetable: Timetable, before: datetime.datetime, now: datetime.datetime
) -> list[tuple[datetime.date, Slot]]:
    """Return the slots that came and went between two looks at the clock.

    Each slot comes with its date, the oldest first. A slot is missed when
    its minute began after the minute that ``before`` lies in, which was
    dealt with at that look, and too long before ``now`` to be still due.
    """
    first = before.replace(second=0, microsecond=0)
    found: list[tuple[datetime.date, Slot]] = []
    date = first.date()
    while date <= now.date():
        for slot in timetable.slots_on(date.weekday()):
            if first < datetime.datetime.combine(date, slot.time) < now - LATE:
                found.append((date, slot))
        date += _DAY
    return found


class Scheduler:
    """``on_due`` and ``on_missed`` are called on the scheduler's thread, so they
    should only hand the slot over, for example by putting it on a queue. A
    scheduler runs once: after ``stop`` it cannot be started again.
    """

    # A week holds every slot once, and a longer gap between two checks is
    # more likely a corrected clock than a sleep. Older slots go unreported.
    _REPORTED = datetime.timedelta(days=7)

    def __init__(
        self,
        timetable: Timetable,
        on_due: Callable[[datetime.date, Slot], None],
        on_missed: Callable[[datetime.date, Slot], None],
        clock: Callable[[], datetime.datetime] = datetime.datetime.now,
        interval: float = 1.0,
    ) -> None:
        self._timetable = timetable
        self._on_due = on_due
        self._on_missed = on_missed
        self._clock = clock
        self._interval = interval  # seconds between two looks at the clock
        self._checked_at: datetime.datetime | None = None
        self._fired: set[datetime.time] = set()
        self._fired_on: datetime.date | None = None
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name="scheduler", daemon=True)

    def set_timetable(self, timetable: Timetable) -> None:
        """Safe to call from any thread. Slots already announced today stay
        announced, so replacing the timetable within a slot's minute does not
        repeat it.
        """
        # Rebinding an attribute is atomic, so no lock is needed.
        self._timetable = timetable

    def start(self) -> None:
        """The slot of the minute that is running is left out: it began before
        the scheduler did, and may have been announced by a run of the app
        that was closed a moment ago.
        """
        self.check()
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread.is_alive():
            self._thread.join()

    def check(self) -> None:
        """The first look only notes the time, as the scheduler begins with it."""
        now = self._clock()
        before, self._checked_at = self._checked_at, now
        minute = now.replace(second=0, microsecond=0)
        if now.date() != self._fired_on:
            self._fired_on = now.date()
            self._fired.clear()
        if before is None:
            self._fired.add(minute.time())
            return
        if before < minute:  # the first look in a new minute
            start = max(before, now - self._REPORTED)
            for date, missed in missed_slots(self._timetable, start, now):
                if date == now.date():
                    self._fired.add(missed.time)
                self._on_missed(date, missed)
        slot = due_slot(self._timetable, now, self._fired)
        if slot is not None:
            self._fired.add(slot.time)
            self._on_due(now.date(), slot)

    def _run(self) -> None:
        while not self._stopped.wait(self._interval):
            try:
                self.check()
            except Exception:
                # One failed check must not silence every later announcement.
                _log.exception("Checking for a due slot failed")
