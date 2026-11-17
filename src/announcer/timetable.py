"""A slot is one minute of a weekday, or of a date, and holds the parts that are
announced in it after one chime: the ends of sessions, then the starts, then
the custom announcements. What the operator changes for a day may move a part
to any minute of that day, where it joins what is already there.
"""

import dataclasses
import datetime
from collections.abc import Iterable
from dataclasses import dataclass

from announcer.models import (
    NORMAL_SPEED,
    SESSION,
    AthleteEntry,
    Change,
    CustomAnnouncement,
    Kind,
)
from announcer.template import render

type _Key = tuple[int, datetime.time]  # weekday and time of a slot
# Which part of a day it is: the moment that it was set for, before the delay,
# its kind and the athlete's name or, for a custom announcement, its text in
# the settings. Neither a delay nor a changed template or schedule changes it,
# nor what the operator has it say for a day.
type PartId = tuple[datetime.datetime, Kind, str]

_SESSION_MINUTES = int(SESSION.total_seconds()) // 60


@dataclass(frozen=True)
class Part:
    """What is said for one athlete, or for one custom announcement."""

    kind: Kind
    text: str  # the sentence to speak
    name: str = ""  # the athlete's; empty for a custom announcement
    # Minutes that it is after the moment it was set for; an edit that moves
    # it to an earlier time makes this negative.
    delay: int = 0
    skipped: bool = False  # by the operator, for the day
    edited: bool = False  # by the operator, for the day
    moved: bool = False  # to a time of its own, so the delay no longer moves it
    custom_text: str = ""


@dataclass(frozen=True)
class Slot:
    weekday: int  # as in ``date.weekday()``
    time: datetime.time
    parts: tuple[Part, ...]  # in the order of ``Kind``
    speed: float = NORMAL_SPEED

    @property
    def texts(self) -> tuple[str, ...]:
        return tuple(part.text for part in self.parts)


@dataclass(frozen=True)
class Timetable:
    slots: tuple[Slot, ...] = ()  # of the week, ordered by weekday, then time
    # The delays that the week's slots were built with; a part that the
    # operator moved for a day has a delay of its own.
    start_delay: int = 0
    end_delay: int = 0
    # The slots of the days that the operator changed, in place of the week's.
    days: tuple[tuple[datetime.date, tuple[Slot, ...]], ...] = ()

    def slots_on(self, date: datetime.date) -> tuple[Slot, ...]:
        for day, slots in self.days:
            if day == date:
                return slots
        return tuple(slot for slot in self.slots if slot.weekday == date.weekday())

    def slot_at(self, date: datetime.date, time: datetime.time) -> Slot | None:
        for slot in self.slots_on(date):
            if slot.time == time:
                return slot
        return None

    def delay_of(self, kind: Kind) -> int:
        if kind is Kind.START:
            return self.start_delay
        return self.end_delay if kind is Kind.END else 0

    def all_slots(self) -> tuple[Slot, ...]:
        """Those of the week and those of the changed days, for what they say."""
        return self.slots + tuple(slot for _, slots in self.days for slot in slots)


def delayed(weekday: int, time: datetime.time, minutes: int) -> _Key:
    days, minute = divmod(time.hour * 60 + time.minute + minutes, 24 * 60)
    return (weekday + days) % 7, datetime.time(*divmod(minute, 60))


def set_for(date: datetime.date, slot: Slot, part: Part) -> datetime.datetime:
    """A change of the delay leaves the returned moment as it is, so it tells
    what a part was before the change.
    """
    due = datetime.datetime.combine(date, slot.time)
    return due - datetime.timedelta(minutes=part.delay)


def part_id(date: datetime.date, slot: Slot, part: Part) -> PartId:
    return set_for(date, slot, part), part.kind, part.name or part.custom_text


def said(text: str, name: str) -> str:
    """Return what is said with ``text`` typed in for the athlete with ``name``,
    in which a placeholder still stands for the name, or for a custom
    announcement, whose text is said as it is written."""
    return render(text, name) if name else text


def build_timetable(
    entries: Iterable[AthleteEntry],
    custom_announcements: Iterable[CustomAnnouncement],
    template: str,
    speed: float = NORMAL_SPEED,
    start_delay: int = 0,
    end_delay: int = 0,
    end_template: str | None = None,
    changes: Iterable[Change] = (),
) -> Timetable:
    """Athletes are announced at the start of their session and, with an
    ``end_template``, at its end too. The delays move the starts and the ends
    only; a custom announcement is read at the time it is set for, with
    whatever else is in that minute. ``changes`` make the days that they are
    for differ from the week.
    """
    entries = tuple(entries)
    listed: dict[tuple[_Key, Kind], tuple[str, ...]] = {
        (key, Kind.START): names for key, names in _starts(entries).items()
    }
    if end_template is not None:
        listed.update({(key, Kind.END): names for key, names in _ends(entries).items()})
    parts: dict[_Key, list[Part]] = {}
    for (key, kind), names in listed.items():
        used = end_template if kind is Kind.END else template
        assert used is not None
        delay = end_delay if kind is Kind.END else start_delay
        parts.setdefault(delayed(*key, delay), []).extend(
            Part(kind, render(used, name), name, delay) for name in names
        )

    for announcement in custom_announcements:
        custom = Part(Kind.CUSTOM, announcement.text, custom_text=announcement.text)
        for weekday in announcement.weekdays:
            in_minute = parts.setdefault((weekday, announcement.time), [])
            if custom not in in_minute:
                in_minute.append(custom)

    slots = tuple(
        sorted(
            (
                Slot(*key, _ordered(key_parts), speed)
                for key, key_parts in parts.items()
            ),
            key=lambda slot: (slot.weekday, slot.time),
        )
    )
    by_day: dict[datetime.date, list[Change]] = {}
    for change in changes:
        by_day.setdefault(change.day, []).append(change)
    days = tuple(
        (day, _day(day, slots, day_changes, speed))
        for day, day_changes in sorted(by_day.items())
    )
    return Timetable(slots, start_delay, end_delay, days)


def _starts(entries: Iterable[AthleteEntry]) -> dict[_Key, tuple[str, ...]]:
    return _names(((entry.weekday, entry.time), entry.name) for entry in entries)


def _ends(entries: Iterable[AthleteEntry]) -> dict[_Key, tuple[str, ...]]:
    return _names(
        (delayed(entry.weekday, entry.time, _SESSION_MINUTES), entry.name)
        for entry in entries
    )


def _names(listed: Iterable[tuple[_Key, str]]) -> dict[_Key, tuple[str, ...]]:
    names: dict[_Key, list[str]] = {}
    for key, name in listed:
        names.setdefault(key, []).append(name)
    # A name repeated within a slot, such as a duplicated row, is said once.
    return {key: tuple(dict.fromkeys(found)) for key, found in names.items()}


def _ordered(parts: Iterable[Part]) -> tuple[Part, ...]:
    """Each kind after the one before it in ``Kind``; within a kind, those of
    the earlier sessions first, and otherwise in the order of the schedule."""
    order = list(Kind)
    return tuple(sorted(parts, key=lambda part: (order.index(part.kind), -part.delay)))


def _day(
    day: datetime.date,
    week: Iterable[Slot],
    changes: Iterable[Change],
    speed: float,
) -> tuple[Slot, ...]:
    """Return the slots of ``day``: those of its weekday, as changed."""
    wanted = {(change.set_for, change.kind, change.name): change for change in changes}
    placed: dict[datetime.time, list[Part]] = {}
    for slot in week:
        if slot.weekday != day.weekday():
            continue
        for part in slot.parts:
            moment = set_for(day, slot, part)
            change = wanted.get(part_id(day, slot, part))
            time = slot.time
            if change is not None:
                part = _changed(part, change, moment)
                if change.time is not None:
                    time = change.time
            placed.setdefault(time, []).append(part)
    return tuple(
        Slot(day.weekday(), time, _ordered(parts), speed)
        for time, parts in sorted(placed.items())
    )


def _changed(part: Part, change: Change, moment: datetime.datetime) -> Part:
    text = part.text if not change.text else said(change.text, part.name)
    delay = part.delay
    if change.time is not None:
        moved = datetime.datetime.combine(change.day, change.time) - moment
        delay = int(moved.total_seconds()) // 60
    return dataclasses.replace(
        part,
        text=text,
        delay=delay,
        skipped=change.skipped,
        edited=change.edited,
        moved=change.time is not None,
    )
