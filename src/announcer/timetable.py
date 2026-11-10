"""Combine athlete sessions and custom announcements into a weekly timetable.

A slot is one weekday and minute. Athletes who start together share a slot. A
custom announcement needs a slot of its own: it may share one neither with
athletes nor with another custom announcement.
"""

import datetime
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from announcer.models import AthleteEntry, CustomAnnouncement
from announcer.template import render

type _Key = tuple[int, datetime.time]  # weekday and time of a slot


class SlotHolder(Enum):
    """What already occupies a slot that a custom announcement asks for."""

    ATHLETES = "athletes"
    CUSTOM_ANNOUNCEMENT = "custom announcement"


@dataclass(frozen=True)
class Conflict:
    """A weekday on which a custom announcement's slot is already taken."""

    announcement: CustomAnnouncement
    weekday: int
    holder: SlotHolder


@dataclass(frozen=True)
class Slot:
    """Everything announced at one weekday and minute, after a single chime."""

    weekday: int  # 0 is Monday, as in ``date.weekday()``
    time: datetime.time
    texts: tuple[str, ...]  # the sentences to speak, in order
    # The athlete behind each text; empty for a custom announcement.
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class Timetable:
    """The week's slots, plus the custom announcements that had to be left out."""

    slots: tuple[Slot, ...] = ()  # ordered by weekday, then time
    conflicts: tuple[Conflict, ...] = ()

    def slot_at(self, weekday: int, time: datetime.time) -> Slot | None:
        """Return the slot at the given weekday and minute, if there is one."""
        for slot in self.slots:
            if slot.weekday == weekday and slot.time == time:
                return slot
        return None

    def slots_on(self, weekday: int) -> tuple[Slot, ...]:
        """Return the slots of the given weekday, ordered by time."""
        return tuple(slot for slot in self.slots if slot.weekday == weekday)


def find_conflicts(
    announcement: CustomAnnouncement,
    entries: Iterable[AthleteEntry],
    others: Iterable[CustomAnnouncement],
) -> tuple[Conflict, ...]:
    """Return the weekdays on which ``announcement`` would share a slot.

    ``others`` are the custom announcements to check against. When an existing
    announcement is being edited, leave its stored version out of them.
    """
    holders = _athlete_slots(entries)
    for other in others:
        for weekday in other.weekdays:
            holders.setdefault((weekday, other.time), SlotHolder.CUSTOM_ANNOUNCEMENT)
    return _conflicts(announcement, holders)


def build_timetable(
    entries: Iterable[AthleteEntry],
    custom_announcements: Iterable[CustomAnnouncement],
    template: str,
) -> Timetable:
    """Build the timetable; ``template`` turns each athlete into a sentence.

    Athletes always keep their slot. A custom announcement is left out on the
    weekdays where its slot is taken, by athletes or by a custom announcement
    earlier in the list, and each such case is reported as a conflict.
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

    slots = tuple(Slot(*key, texts[key], names.get(key, ())) for key in sorted(texts))
    return Timetable(slots, tuple(conflicts))


def _athlete_slots(entries: Iterable[AthleteEntry]) -> dict[_Key, SlotHolder]:
    """Return the slots that athletes occupy."""
    return {(entry.weekday, entry.time): SlotHolder.ATHLETES for entry in entries}


def _conflicts(
    announcement: CustomAnnouncement, holders: Mapping[_Key, SlotHolder]
) -> tuple[Conflict, ...]:
    """Return the weekdays, in order, on which ``announcement``'s slot is held."""
    return tuple(
        Conflict(announcement, weekday, holders[weekday, announcement.time])
        for weekday in sorted(announcement.weekdays)
        if (weekday, announcement.time) in holders
    )
