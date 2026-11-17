"""A slot is announced at its time or not at all: by a scheduler that was already
running when the slot's minute began, at most half a minute late, and once per
date. A slot whose minute began while the app was closed is not caught up on.
Neither is one that the app could not announce in time although it ran,
because the computer was asleep; that one is reported as missed, so the app
can show it. A change of a delay neither repeats a slot nor drops one: what
a shorter delay puts behind the clock is announced at once. Each part of a
slot is dealt with once, so a slot is handed over with only the parts that
are still to be announced, and none that the operator skipped.
"""

import dataclasses
import datetime
import logging
import threading
from collections.abc import Callable, Collection, Iterator, Mapping

from announcer.models import Kind
from announcer.timetable import PartId, Slot, Timetable, part_id

_log = logging.getLogger(__name__)

_DAY = datetime.timedelta(days=1)
# How long into its minute a slot may still be announced. A second or two is
# normal; more happens when the computer is very busy or wakes from a sleep.
LATE = datetime.timedelta(seconds=30)


def due_slot(
    timetable: Timetable, now: datetime.datetime, fired: Collection[PartId]
) -> Slot | None:
    """``fired`` holds the parts already dealt with."""
    minute = now.replace(second=0, microsecond=0)
    if now - minute > LATE:
        return None
    slot = timetable.slot_at(now.date(), minute.time())
    return None if slot is None else untaken(now.date(), slot, fired)


def untaken(date: datetime.date, slot: Slot, fired: Collection[PartId]) -> Slot | None:
    parts = tuple(
        part
        for part in slot.parts
        if not part.skipped and part_id(date, slot, part) not in fired
    )
    return dataclasses.replace(slot, parts=parts) if parts else None


def missed_slots(
    timetable: Timetable, before: datetime.datetime, now: datetime.datetime
) -> list[tuple[datetime.date, Slot]]:
    """Return the slots that came and went between two looks at the clock.

    Each slot comes with its date, the oldest first. A slot is missed when
    its minute began after the minute that ``before`` lies in, which was
    dealt with at that look, and too long before ``now`` to be still due.
    """
    first = before.replace(second=0, microsecond=0)
    return [
        (due.date(), slot)
        for due, slot in _slots_from(timetable, first.date(), now.date())
        if first < due < now - LATE
    ]


def brought_forward(
    timetable: Timetable,
    before: datetime.datetime,
    by: Mapping[Kind, datetime.timedelta],
) -> list[tuple[datetime.date, Slot]]:
    """Return the slots that the delay of a kind, made shorter ``by`` that
    much, has moved from after ``before`` to that moment or earlier. Each slot
    comes with its date, the oldest first, and with only the parts that the
    delay moved: one that the operator put at a time of its own stays there.
    """
    none = datetime.timedelta()
    most = max(by.values(), default=none)
    if most <= none:
        return []
    found = []
    for due, slot in _slots_from(timetable, (before - most).date(), before.date()):
        parts = tuple(
            part
            for part in slot.parts
            if not part.moved and before - by.get(part.kind, none) < due <= before
        )
        if parts:
            found.append((due.date(), dataclasses.replace(slot, parts=parts)))
    return found


def _slots_from(
    timetable: Timetable, first: datetime.date, last: datetime.date
) -> Iterator[tuple[datetime.datetime, Slot]]:
    date = first
    while date <= last:
        for slot in timetable.slots_on(date):
            yield datetime.datetime.combine(date, slot.time), slot
        date += _DAY


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
        # Of the timetable at the last look.
        self._delays = dict.fromkeys(Kind, datetime.timedelta())
        # The parts dealt with, each with when that was.
        self._fired: dict[PartId, datetime.datetime] = {}
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name="scheduler", daemon=True)

    def set_timetable(self, timetable: Timetable) -> None:
        """Safe to call from any thread. Slots already announced today stay
        announced, so replacing the timetable within a slot's minute does not
        repeat it.
        """
        # Rebinding an attribute is atomic, so no lock is needed.
        self._timetable = timetable

    def start(self) -> datetime.datetime:
        """Return the moment of the first look. The slot of the minute that is
        running then is left out: it began before the scheduler did, and may
        have been announced by a run of the app that was closed a moment ago.
        So set the timetable first.
        """
        self.check()
        self._thread.start()
        assert self._checked_at is not None
        return self._checked_at

    def stop(self) -> None:
        self._stopped.set()
        if self._thread.is_alive():
            self._thread.join()

    def check(self) -> None:
        """The first look announces nothing: it notes the time and leaves out the
        slot of its minute."""
        now = self._clock()
        before, self._checked_at = self._checked_at, now
        timetable = self._timetable  # the same one for the whole look
        delays = {
            kind: datetime.timedelta(minutes=timetable.delay_of(kind)) for kind in Kind
        }
        shorter_by = {kind: self._delays[kind] - delays[kind] for kind in Kind}
        self._delays = delays
        minute = now.replace(second=0, microsecond=0)
        # A part dealt with that long ago is not due again: neither a delay
        # nor a change of the day moves it that far once it was due.
        self._fired = {
            fired: when for fired, when in self._fired.items() if now - when < _DAY
        }
        if before is None:
            slot = timetable.slot_at(now.date(), minute.time())
            if slot is not None:
                self._take(now.date(), slot)
            return
        for date, late in brought_forward(timetable, before, shorter_by):
            taken = self._take(date, late)
            if taken is not None:
                self._on_due(date, taken)
        if before < minute:  # the first look in a new minute
            start = max(before, now - self._REPORTED)
            for date, missed in missed_slots(timetable, start, now):
                taken = self._take(date, missed)
                if taken is not None:
                    self._on_missed(date, taken)
        slot = due_slot(timetable, now, self._fired)
        taken = None if slot is None else self._take(now.date(), slot)
        if taken is not None:
            self._on_due(now.date(), taken)

    def _take(self, date: datetime.date, slot: Slot) -> Slot | None:
        """Return the slot with the parts that are still to be dealt with, which
        are not after this, or None if there are none."""
        taken = untaken(date, slot, self._fired)
        if taken is not None:
            now = self._clock()
            self._fired.update(
                (part_id(date, taken, part), now) for part in taken.parts
            )
        return taken

    def _run(self) -> None:
        while not self._stopped.wait(self._interval):
            try:
                self.check()
            except Exception:
                # One failed check must not silence every later announcement.
                _log.exception("Checking for a due slot failed")
