"""Decide when a slot is due and report it from a background thread.

A slot fires only during its own minute and at most once per date. A slot
whose minute passed while the app was closed, or the computer asleep, is not
caught up on.
"""

import datetime
import threading
from collections.abc import Callable, Collection

from announcer.timetable import Slot, Timetable


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


class Scheduler:
    """Check the clock on a background thread and report each due slot once.

    ``on_due`` is called on that thread, so it should only hand the slot over,
    for example by putting it on a queue.
    """

    def __init__(
        self,
        timetable: Timetable,
        on_due: Callable[[Slot], None],
        clock: Callable[[], datetime.datetime] = datetime.datetime.now,
        interval: float = 1.0,
    ) -> None:
        self._timetable = timetable
        self._on_due = on_due
        self._clock = clock
        self._interval = interval  # seconds between two looks at the clock
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
        """Look at the clock once and report the slot that is due, if any."""
        now = self._clock()
        if now.date() != self._fired_on:
            self._fired_on = now.date()
            self._fired.clear()
        slot = due_slot(self._timetable, now, self._fired)
        if slot is not None:
            self._fired.add(slot.time)
            self._on_due(slot)

    def _run(self) -> None:
        while not self._stopped.wait(self._interval):
            self.check()
