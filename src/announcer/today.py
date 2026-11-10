"""Today's announcements and how each one fared, for the preview in the window."""

import datetime
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from announcer.player import Playback
from announcer.scheduler import LATE
from announcer.timetable import Slot, Timetable


class Status(Enum):
    """Where an announcement stands. The UI turns these into Turkish words."""

    WAITING = "waiting"  # its time has not come yet
    NEXT = "next"  # the first one whose time has not come yet
    PASSED = "passed"  # its time went by without the app being there for it
    PLAYING = "playing"  # due, and queued or sounding right now
    PLAYED = "played"  # every text was heard
    PARTLY_PLAYED = "partly played"  # some texts were heard, the others skipped
    NO_SPEECH = "no speech"  # skipped: none of its texts has stored speech
    ASLEEP = "asleep"  # skipped: the computer was asleep at its time
    NO_OUTPUT = "no output"  # the audio output could not play it, or not all
    CONFLICT = "conflict"  # skipped today: its slot belongs to something else


@dataclass(frozen=True)
class Row:
    """One line of today's preview."""

    time: datetime.time
    athletes: int  # how many athletes it names; none for a custom announcement
    summary: str  # the athletes' names, or the custom announcement's text
    status: Status


def summary(slot: Slot) -> str:
    """Return the athletes' names of a slot, or its custom announcement's text."""
    return ", ".join(slot.names) if slot.names else slot.texts[0]


def outcome_status(playback: Playback) -> Status:
    """Return how the announcement behind ``playback`` fared."""
    skipped = len(playback.missing) + len(playback.late)
    if playback.failed:
        return Status.NO_OUTPUT
    if not skipped:
        return Status.PLAYED
    if skipped < len(playback.texts):
        return Status.PARTLY_PLAYED
    return Status.ASLEEP if playback.late else Status.NO_SPEECH


def todays_rows(
    timetable: Timetable,
    now: datetime.datetime,
    outcomes: Mapping[tuple[datetime.datetime, str], Status],
) -> tuple[Row, ...]:
    """Return the announcements of ``now``'s date, ordered by time.

    ``outcomes`` holds what is known about the slots that came due, keyed by
    the moment they were due and their summary, so an outcome is not taken
    for that of something else that was put at the same time later. The
    custom announcements that lost today's slot to something else are listed
    too, so that the preview hides nothing.
    """
    rows: list[Row] = []
    upcoming = Status.NEXT
    for slot in timetable.slots_on(now.weekday()):
        due = datetime.datetime.combine(now.date(), slot.time)
        status = outcomes.get((due, summary(slot)))
        if status is None and now - due > LATE:
            status = Status.PASSED  # too late for the scheduler to announce it
        elif status is None:
            status, upcoming = upcoming, Status.WAITING
        rows.append(Row(slot.time, len(slot.names), summary(slot), status))
    rows.extend(
        Row(
            conflict.announcement.time,
            0,
            conflict.announcement.text,
            Status.CONFLICT,
        )
        for conflict in timetable.conflicts
        if conflict.weekday == now.weekday()
    )
    # The sort is stable, so a skipped announcement follows the slot that won.
    return tuple(sorted(rows, key=lambda row: row.time))
