import datetime
from dataclasses import dataclass
from enum import Enum

# Indexed like ``date.weekday()``.
WEEKDAY_NAMES: tuple[str, ...] = (
    "Pazartesi",
    "Salı",
    "Çarşamba",
    "Perşembe",
    "Cuma",
    "Cumartesi",
    "Pazar",
)
ALL_WEEKDAYS: frozenset[int] = frozenset(range(len(WEEKDAY_NAMES)))

# Multiples of the voice's own speed.
NORMAL_SPEED = 1.0
SPEEDS: tuple[float, ...] = (0.8, 0.9, NORMAL_SPEED, 1.1, 1.2)
# How many minutes after its time the start or the end of a session can be
# announced.
DELAYS: tuple[int, ...] = (0, 1, 2, 3, 5, 10)
# How long a training session lasts; its end is announced that long after its start.
SESSION = datetime.timedelta(minutes=55)


class Kind(Enum):
    """What an announcement is for. Within a minute they are read in this order:
    those whose session ends say goodbye before the next ones are welcomed.
    """

    END = "end"  # athletes whose session ends
    START = "start"  # athletes whose session starts
    CUSTOM = "custom"
    INSTANT = "instant"  # typed by the operator and read at once


@dataclass(frozen=True)
class AthleteEntry:
    name: str
    weekday: int  # as in ``date.weekday()``
    time: datetime.time  # start of the session, to the minute


@dataclass(frozen=True)
class CustomAnnouncement:
    text: str
    time: datetime.time
    weekdays: frozenset[int] = ALL_WEEKDAYS  # as in ``date.weekday()``

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("The announcement has no text")
        if self.time != datetime.time(self.time.hour, self.time.minute):
            raise ValueError("The time must be a plain hour and minute")
        if not self.weekdays or not self.weekdays <= ALL_WEEKDAYS:
            raise ValueError("The weekdays must be one or more of 0 to 6")


@dataclass(frozen=True)
class Change:
    """What the operator changed, for one day only, about the announcement of
    one athlete or one custom announcement. ``set_for``, ``kind`` and ``name``
    tell which: the moment that it was set for, before the delay, stays the
    same whatever else changes.
    """

    day: datetime.date  # the day that it is announced on
    set_for: datetime.datetime
    kind: Kind
    # The athlete's; for a custom announcement, its text in the settings.
    name: str = ""
    skipped: bool = False
    time: datetime.time | None = None  # when it is announced that day instead
    text: str | None = None  # what is said instead

    @property
    def edited(self) -> bool:
        return self.time is not None or self.text is not None
