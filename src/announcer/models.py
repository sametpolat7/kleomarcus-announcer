import datetime
from dataclasses import dataclass

# Turkish weekday names indexed like ``date.weekday()``: 0 is Monday.
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
# How many minutes after its time an announcement can be made.
DELAYS: tuple[int, ...] = (0, 1, 2, 3, 5, 10)


@dataclass(frozen=True)
class AthleteEntry:
    name: str
    weekday: int  # 0 is Monday, as in ``date.weekday()``
    time: datetime.time  # start of the session, to the minute


@dataclass(frozen=True)
class CustomAnnouncement:
    text: str
    time: datetime.time  # to the minute
    weekdays: frozenset[int] = ALL_WEEKDAYS  # as in ``date.weekday()``

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("The announcement has no text")
        if self.time != datetime.time(self.time.hour, self.time.minute):
            raise ValueError("The time must be a plain hour and minute")
        if not self.weekdays or not self.weekdays <= ALL_WEEKDAYS:
            raise ValueError("The weekdays must be one or more of 0 to 6")
