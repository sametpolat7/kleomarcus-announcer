import datetime
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from announcer.models import Kind
from announcer.player import Heard, Playback
from announcer.scheduler import LATE
from announcer.timetable import Part, PartId, Timetable, delayed, set_for


class Status(Enum):
    """The UI turns these into Turkish words."""

    WAITING = "waiting"  # its time has not come yet
    NEXT = "next"  # among the first ones whose time has not come yet
    PASSED = "passed"  # its time went by without the app being there for it
    PREPARING = "preparing"  # its speech is being made, to be read at once
    QUEUED = "queued"  # due, and waiting for what is being read before it
    PLAYING = "playing"
    PLAYED = "played"  # every text was heard
    PARTLY_PLAYED = "partly played"  # some texts were heard, the others skipped
    NO_SPEECH = "no speech"  # skipped: none of its texts has stored speech
    ASLEEP = "asleep"  # skipped: the computer was asleep at its time
    NO_OUTPUT = "no output"  # the audio output could not play it, or not all
    CONFLICT = "conflict"  # skipped today: its slot belongs to something else
    SKIPPING = "skipping"  # the operator skipped it, and its time has not come
    SKIPPED = "skipped"  # the operator skipped it, and its time went by
    STOPPED = "stopped"  # the operator cut it off before it was heard in full


# A conflict is among them because it has no slot whose speech could be played.
_NOT_REPLAYABLE = frozenset(
    {
        Status.WAITING,
        Status.NEXT,
        Status.PREPARING,
        Status.QUEUED,
        Status.PLAYING,
        Status.CONFLICT,
        Status.SKIPPING,
    }
)
BUSY = frozenset({Status.PREPARING, Status.QUEUED, Status.PLAYING})
# How long before its minute an announcement can no longer be skipped or
# changed, and before the end of the time that it can still be read in, a
# skip no longer undone: the scheduler looks every second, and a change has
# to be in place before it looks.
CHANGE_MARGIN = datetime.timedelta(seconds=2)

# The moment that a part was set for, or that an instant announcement was asked
# for, its kind and its summary: what an outcome is of.
type OutcomeKey = tuple[datetime.datetime, Kind, str]


@dataclass(frozen=True)
class Instant:
    """An announcement that the operator typed and had read at once."""

    asked_at: datetime.datetime
    text: str

    @property
    def key(self) -> OutcomeKey:
        return self.asked_at, Kind.INSTANT, self.text


@dataclass(frozen=True)
class Row:
    time: datetime.time  # when it is announced, so with the delay
    kind: Kind
    text: str  # what is read
    status: Status
    # Which part of the day it is, or which instant announcement; None for a
    # custom announcement that lost its slot.
    key: PartId | Instant | None = None
    edited: bool = False  # by the operator, for today
    # Whether it can still be changed, or skipped or no longer skipped.
    editable: bool = False
    skippable: bool = False
    # Its minute is still being read, though it may have been heard already.
    in_group: bool = False

    @property
    def replayable(self) -> bool:
        return not self.in_group and self.status not in _NOT_REPLAYABLE


def who(part: Part) -> str:
    """Return whom a part is for: the athlete, or the custom announcement's
    text. An outcome is kept under it, so that it stays with the athlete when
    the template changes."""
    return part.name or part.text


def outcome_status(playback: Playback) -> Status:
    skipped = len(playback.missing) + len(playback.late)
    if playback.failed:
        return Status.NO_OUTPUT
    if playback.cut:
        return Status.STOPPED
    if not skipped:
        return Status.PLAYED
    if skipped < len(playback.texts):
        return Status.PARTLY_PLAYED
    return Status.ASLEEP if playback.late else Status.NO_SPEECH


_HEARD: dict[Heard, Status] = {
    Heard.YES: Status.PLAYED,
    Heard.NO_SPEECH: Status.NO_SPEECH,
    Heard.UNPLAYABLE: Status.NO_OUTPUT,
    Heard.LATE: Status.ASLEEP,
    Heard.CUT: Status.STOPPED,
}


def part_status(playback: Playback, index: int) -> Status:
    return _HEARD[playback.heard[index]]


def todays_rows(
    timetable: Timetable,
    now: datetime.datetime,
    outcomes: Mapping[OutcomeKey, Status],
    instants: Iterable[Instant] = (),
    playing: Collection[OutcomeKey] = (),
) -> tuple[Row, ...]:
    """``outcomes`` holds what is known about the parts that came due, keyed by
    the moment they were set for, their kind and ``who`` they are for, so an
    outcome is not taken for that of something else that was put at the same
    time later, and stays with its part when the delay or the template
    changes. The custom announcements that lost today's slot to something else
    are listed too, so that the preview hides nothing. ``playing`` holds the
    outcome keys of what is queued or being read.
    """
    rows: list[Row] = []
    upcoming: datetime.time | None = None  # the first minute with something to say
    for slot in timetable.slots_on(now.date()):
        due = datetime.datetime.combine(now.date(), slot.time)
        early = now < due - CHANGE_MARGIN
        for part in slot.parts:
            moment = set_for(now.date(), slot, part)
            # What happened to it beats what was planned for it.
            status = outcomes.get((moment, part.kind, who(part)))
            skippable = editable = False
            if status is None and part.skipped:
                status = Status.SKIPPED if now - due > LATE else Status.SKIPPING
                skippable = now - due <= LATE - CHANGE_MARGIN
                editable = early
            elif status is None and now - due > LATE:
                status = Status.PASSED  # too late for the scheduler to announce it
            elif status is None:
                if upcoming is None:
                    upcoming = slot.time
                status = Status.NEXT if slot.time == upcoming else Status.WAITING
                skippable = editable = early
            rows.append(
                Row(
                    slot.time,
                    part.kind,
                    part.text,
                    status,
                    (moment, part.kind, part.name),
                    part.edited,
                    editable,
                    skippable,
                    (moment, part.kind, who(part)) in playing,
                )
            )
    for conflict in timetable.conflicts:
        announcement = conflict.announcement
        weekday, time = delayed(conflict.weekday, announcement.time, timetable.delay)
        if weekday == now.weekday():
            rows.append(Row(time, Kind.CUSTOM, announcement.text, Status.CONFLICT))
    for instant in instants:
        if instant.asked_at.date() == now.date():
            status = outcomes.get(instant.key, Status.PREPARING)
            rows.append(
                Row(
                    instant.asked_at.time(),
                    Kind.INSTANT,
                    instant.text,
                    status,
                    instant,
                    in_group=instant.key in playing,
                )
            )
    # The sort is stable, so a skipped announcement follows the slot that won.
    return tuple(sorted(rows, key=lambda row: row.time))
