"""Decide when a slot is due and report it from a background thread.

A slot fires only during its own minute and at most once per date. A slot
whose minute passed while the app was closed is not caught up on. Neither is
one whose minute passed while the app ran but the computer was asleep; that
one is reported as missed, so the app can show it.
"""

import datetime
import logging
import threading
from collections.abc import Callable, Collection

from announcer.timetable import Slot, Timetable

_log = logging.getLogger(__name__)

_MINUTE = datetime.timedelta(minutes=1)
_DAY = datetime.timedelta(days=1)


def due_slot(
    timetable: Timetable, now: datetime.datetime, fired: Collection[datetime.time]
) -> Slot | None:
    """Return the slot to announce at ``now``, if there is one.

    ``fired`` holds the times of the slots already announced on ``now``'s date.
    """
    minute = datetime.time(now.hour, now.minute)
    if minute in fired:
        return None
    return timetable.slot_at(now.weekday(), minute)


def missed_slots(
    timetable: Timetable, before: datetime.datetime, now: datetime.datetime
) -> list[tuple[datetime.date, Slot]]:
    """Return the slots whose whole minute went by between two looks at the clock.

    Each slot comes with its date, the oldest first. The slot of the minute
    that ``before`` lies in was dealt with at that look, and the slot of
    ``now``'s minute is still due, so neither counts.
    """
    first = before.replace(second=0, microsecond=0)
    last = now.replace(second=0, microsecond=0)
    found: list[tuple[datetime.date, Slot]] = []
    date = first.date()
    while date <= last.date():
        for slot in timetable.slots_on(date.weekday()):
            if first < datetime.datetime.combine(date, slot.time) < last:
                found.append((date, slot))
        date += _DAY
    return found


class Scheduler:
    """Check the clock on a background thread and report each due slot once.

    ``on_due`` gets the date and the slot that is due now. ``on_missed`` gets
    the date and the slot of each minute that went by between two checks,
    which happens when the computer sleeps. Both are called on the scheduler's
    thread, so they should only hand the slot over, for example by putting it
    on a queue. A scheduler runs once: after ``stop`` it cannot be started
    again.
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
        """Use ``timetable`` from the next check on; safe to call from any thread.

        Slots already announced today stay announced, so replacing the
        timetable within a slot's minute does not repeat it.
        """
        # Rebinding an attribute is atomic, so no lock is needed.
        self._timetable = timetable

    def start(self) -> None:
        """Start checking the clock."""
        self._thread.start()

    def stop(self) -> None:
        """Stop checking the clock and wait for the thread to end."""
        self._stopped.set()
        if self._thread.is_alive():
            self._thread.join()

    def check(self) -> None:
        """Look at the clock once; report the slots missed since the last look
        and the slot that is due now, if any."""
        now = self._clock()
        before, self._checked_at = self._checked_at, now
        if before is not None and now - before >= _MINUTE:
            start = max(before, now - self._REPORTED)
            for date, missed in missed_slots(self._timetable, start, now):
                self._on_missed(date, missed)
        if now.date() != self._fired_on:
            self._fired_on = now.date()
            self._fired.clear()
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
