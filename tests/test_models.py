"""Tests for the domain types."""

import datetime

import pytest

from announcer.models import ALL_WEEKDAYS, WEEKDAY_NAMES, CustomAnnouncement

NOON = datetime.time(12, 0)


def test_weekday_names_follow_date_weekday() -> None:
    monday = datetime.date(2026, 11, 9)

    assert WEEKDAY_NAMES[monday.weekday()] == "Pazartesi"
    assert len(WEEKDAY_NAMES) == 7


def test_custom_announcement_repeats_every_day_by_default() -> None:
    announcement = CustomAnnouncement("Salon 22:00'de kapanacaktır.", NOON)

    assert announcement.weekdays == ALL_WEEKDAYS == frozenset(range(7))


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_custom_announcement_needs_text(text: str) -> None:
    with pytest.raises(ValueError, match="text"):
        CustomAnnouncement(text, NOON)


@pytest.mark.parametrize(
    "time",
    [
        datetime.time(12, 0, 30),
        datetime.time(12, 0, 0, 500),
        datetime.time(12, 0, tzinfo=datetime.UTC),
    ],
)
def test_custom_announcement_needs_a_plain_hour_and_minute(
    time: datetime.time,
) -> None:
    with pytest.raises(ValueError, match="time"):
        CustomAnnouncement("Duyuru", time)


@pytest.mark.parametrize(
    "weekdays",
    [frozenset[int](), frozenset({7}), frozenset({-1, 0}), frozenset({0, 9})],
)
def test_custom_announcement_needs_valid_weekdays(weekdays: frozenset[int]) -> None:
    with pytest.raises(ValueError, match="weekdays"):
        CustomAnnouncement("Duyuru", NOON, weekdays)
