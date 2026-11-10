"""Domain types shared across the app."""

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


@dataclass(frozen=True)
class AthleteEntry:
    """One athlete's training session, as read from the schedule file."""

    name: str
    weekday: int  # 0 is Monday, as in ``date.weekday()``
    time: datetime.time  # start of the session, to the minute
