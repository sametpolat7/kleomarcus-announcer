"""A slot is one weekday and minute. Athletes who start together share a slot. A
custom announcement needs a slot of its own: it may share one neither with
athletes nor with another custom announcement.
"""

import datetime
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from announcer.models import NORMAL_SPEED, AthleteEntry, CustomAnnouncement
from announcer.template import render

type _Key = tuple[int, datetime.time]  # weekday and time of a slot


class SlotHolder(Enum):
    ATHLETES = "athletes"
    CUSTOM_ANNOUNCEMENT = "custom announcement"


@dataclass(frozen=True)
class Conflict:
    announcement: CustomAnnouncement
    weekday: int
    holder: SlotHolder


@dataclass(frozen=True)
class Slot:
    weekday: int  # 0 is Monday, as in ``date.weekday()``
    time: datetime.time
    texts: tuple[str, ...]  # the sentences to speak, in order
    # The athlete behind each text; empty for a custom announcement.
    names: tuple[str, ...] = ()
    speed: float = NORMAL_SPEED
    delay: int = 0  # minutes that it is after the time it was set for


@dataclass(frozen=True)
class Timetable:
    slots: tuple[Slot, ...] = ()  # ordered by weekday, then time
    # Each with the weekday and the time that its announcement was set for.
    conflicts: tuple[Conflict, ...] = ()
    delay: int = 0  # minutes that every slot is after the time it was set for

    def slot_at(self, weekday: int, time: datetime.time) -> Slot | None:
        for slot in self.slots:
            if slot.weekday == weekday and slot.time == time:
                return slot
        return None

    def slots_on(self, weekday: int) -> tuple[Slot, ...]:
        return tuple(slot for slot in self.slots if slot.weekday == weekday)


def delayed(weekday: int, time: datetime.time, minutes: int) -> _Key:
    days, minute = divmod(time.hour * 60 + time.minute + minutes, 24 * 60)
    return (weekday + days) % 7, datetime.time(*divmod(minute, 60))


def set_for(date: datetime.date, slot: Slot) -> datetime.datetime:
    """Return the moment that the slot of ``date`` was set for. A change of the
    delay leaves it as it is, so it tells what a slot was before the change.
    """
    due = datetime.datetime.combine(date, slot.time)
    return due - datetime.timedelta(minutes=slot.delay)


def find_conflicts(
    announcement: CustomAnnouncement,
    entries: Iterable[AthleteEntry],
    others: Iterable[CustomAnnouncement],
) -> tuple[Conflict, ...]:
    """When an existing announcement is being edited, leave its stored version
    out of ``others``.
    """
    holders = {(entry.weekday, entry.time): SlotHolder.ATHLETES for entry in entries}
    for other in others:
        for weekday in other.weekdays:
            holders.setdefault((weekday, other.time), SlotHolder.CUSTOM_ANNOUNCEMENT)
    return _conflicts(announcement, holders)


def build_timetable(
    entries: Iterable[AthleteEntry],
    custom_announcements: Iterable[CustomAnnouncement],
    template: str,
    speed: float = NORMAL_SPEED,
    delay: int = 0,
) -> Timetable:
    """Athletes always keep their slot. A custom announcement is left out on the
    weekdays where its slot is taken, by athletes or by a custom announcement
    earlier in the list, and each such case is reported as a conflict. The
    delay moves everything alike, so it changes nothing about that.
    """
    listed: dict[_Key, list[str]] = {}
    for entry in entries:
        listed.setdefault((entry.weekday, entry.time), []).append(entry.name)
    # A name repeated within a slot, such as a duplicated row, is said once.
    names = {
        key: tuple(dict.fromkeys(slot_names)) for key, slot_names in listed.items()
    }
    texts: dict[_Key, tuple[str, ...]] = {
        key: tuple(render(template, name) for name in slot_names)
        for key, slot_names in names.items()
    }

    holders = dict.fromkeys(texts, SlotHolder.ATHLETES)
    conflicts: list[Conflict] = []
    for announcement in custom_announcements:
        found = _conflicts(announcement, holders)
        conflicts.extend(found)
        taken = {conflict.weekday for conflict in found}
        for weekday in announcement.weekdays - taken:
            key = (weekday, announcement.time)
            texts[key] = (announcement.text,)
            holders[key] = SlotHolder.CUSTOM_ANNOUNCEMENT

    slots = sorted(
        (
            Slot(*delayed(*key, delay), texts[key], names.get(key, ()), speed, delay)
            for key in texts
        ),
        key=lambda slot: (slot.weekday, slot.time),
    )
    return Timetable(tuple(slots), tuple(conflicts), delay)


def _conflicts(
    announcement: CustomAnnouncement, holders: Mapping[_Key, SlotHolder]
) -> tuple[Conflict, ...]:
    return tuple(
        Conflict(announcement, weekday, holders[weekday, announcement.time])
        for weekday in sorted(announcement.weekdays)
        if (weekday, announcement.time) in holders
    )
