import datetime
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from announcer.player import Playback
from announcer.scheduler import LATE
from announcer.timetable import Slot, Timetable, delayed, set_for


class Status(Enum):
    """The UI turns these into Turkish words."""

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


# A conflict is among them because it has no slot whose speech could be played.
_NOT_REPLAYABLE = frozenset(
    {Status.WAITING, Status.NEXT, Status.PLAYING, Status.CONFLICT}
)


@dataclass(frozen=True)
class Row:
    time: datetime.time  # when it is announced, so with the delay
    summary: str  # the athletes' names, or the custom announcement's text
    status: Status

    @property
    def replayable(self) -> bool:
        return self.status not in _NOT_REPLAYABLE


def summary(slot: Slot) -> str:
    return ", ".join(slot.names) if slot.names else slot.texts[0]


def outcome_status(playback: Playback) -> Status:
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
    """``outcomes`` holds what is known about the slots that came due, keyed by
    the moment they were set for and their summary, so an outcome is not taken
    for that of something else that was put at the same time later, and stays
    with its slot when the delay changes. The custom announcements that lost
    today's slot to something else are listed too, so that the preview hides
    nothing.
    """
    rows: list[Row] = []
    upcoming = Status.NEXT
    for slot in timetable.slots_on(now.weekday()):
        due = datetime.datetime.combine(now.date(), slot.time)
        text = summary(slot)
        status = outcomes.get((set_for(now.date(), slot), text))
        if status is None and now - due > LATE:
            status = Status.PASSED  # too late for the scheduler to announce it
        elif status is None:
            status, upcoming = upcoming, Status.WAITING
        rows.append(Row(slot.time, text, status))
    for conflict in timetable.conflicts:
        announcement = conflict.announcement
        weekday, time = delayed(conflict.weekday, announcement.time, timetable.delay)
        if weekday == now.weekday():
            rows.append(Row(time, announcement.text, Status.CONFLICT))
    # The sort is stable, so a skipped announcement follows the slot that won.
    return tuple(sorted(rows, key=lambda row: row.time))
