"""Tests for the domain types."""

import datetime

import pytest

from announcer.models import WEEKDAY_NAMES, CustomAnnouncement

NOON = datetime.time(12, 0)
MONDAY = frozenset({0})


def test_weekday_names_follow_date_weekday() -> None:
    monday = datetime.date(2026, 11, 9)

    assert WEEKDAY_NAMES[monday.weekday()] == "Pazartesi"
    assert WEEKDAY_NAMES[6] == "Pazar"


@pytest.mark.parametrize(
    ("text", "time", "weekdays", "problem"),
    [
        ("   ", NOON, MONDAY, "text"),
        ("Duyuru", datetime.time(12, 0, 30), MONDAY, "time"),
        ("Duyuru", datetime.time(12, 0, tzinfo=datetime.UTC), MONDAY, "time"),
        ("Duyuru", NOON, frozenset[int](), "weekdays"),
        ("Duyuru", NOON, frozenset({0, 7}), "weekdays"),
    ],
)
def test_custom_announcement_that_cannot_be_announced_is_rejected(
    text: str, time: datetime.time, weekdays: frozenset[int], problem: str
) -> None:
    with pytest.raises(ValueError, match=problem):
        CustomAnnouncement(text, time, weekdays)
