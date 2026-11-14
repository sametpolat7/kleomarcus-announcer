import datetime
import json
from pathlib import Path

import pytest

from announcer.changes import ChangesFileError, load_changes, save_changes
from announcer.models import Change, Kind

MONDAY = datetime.date(2026, 11, 9)
TUESDAY = MONDAY + datetime.timedelta(days=1)
SIX_PM = datetime.datetime.combine(MONDAY, datetime.time(18, 0))
SEVEN_PM = SIX_PM + datetime.timedelta(hours=1)

SKIPPED = Change(MONDAY, SIX_PM, Kind.START, "Ayşe Yılmaz", skipped=True)
EDITED = Change(
    MONDAY,
    SEVEN_PM,
    Kind.END,
    "Mehmet Demir",
    time=datetime.time(19, 5),
    text="{ad_soyad}, görüşmek üzere.",
)
CUSTOM = Change(MONDAY, SEVEN_PM, Kind.CUSTOM, text="Su içmeyi unutmayın lütfen.")

# Each unusable change below differs from this one in one way.
USABLE = {
    "day": "2026-11-09",
    "set_for": "2026-11-09 18:00",
    "kind": "start",
    "name": "Ayşe Yılmaz",
}


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "changes.json"


def test_saved_changes_load_again(path: Path) -> None:
    save_changes((SKIPPED, EDITED, CUSTOM), path)

    assert load_changes(path, MONDAY) == (SKIPPED, EDITED, CUSTOM)
    assert "görüşmek" in path.read_text(encoding="utf-8")


def test_each_change_is_saved_with_the_athlete_it_is_for(path: Path) -> None:
    save_changes((SKIPPED, EDITED, CUSTOM), path)

    stored = json.loads(path.read_text(encoding="utf-8"))["changes"]

    assert [change["name"] for change in stored] == ["Ayşe Yılmaz", "Mehmet Demir", ""]
    assert all("left_out" not in change for change in stored)


def test_changes_of_two_athletes_of_the_same_minute_are_kept_apart(
    path: Path,
) -> None:
    other = Change(MONDAY, SIX_PM, Kind.START, "Mehmet Demir", text="Hoş geldin.")
    save_changes((SKIPPED, other), path)

    assert load_changes(path, MONDAY) == (SKIPPED, other)


def test_custom_change_saved_without_a_name_loads(path: Path) -> None:
    stored = {"day": "2026-11-09", "set_for": "2026-11-09 19:00", "kind": "custom"}
    path.write_text(json.dumps({"changes": [{**stored, "skipped": True}]}))

    assert load_changes(path, MONDAY) == (
        Change(MONDAY, SEVEN_PM, Kind.CUSTOM, skipped=True),
    )


def test_no_file_means_no_changes(path: Path) -> None:
    assert load_changes(path, MONDAY) == ()


def test_changes_of_a_day_that_is_over_are_let_go_of(path: Path) -> None:
    save_changes((SKIPPED, EDITED, CUSTOM), path)

    assert load_changes(path, TUESDAY) == ()


@pytest.mark.parametrize(
    "content",
    [b"{", b"[]", b'{"changes": 3}', b"\xff\xfe\x00"],
    ids=["cut short", "not an object", "no list", "not utf-8"],
)
def test_unreadable_file_is_an_error(path: Path, content: bytes) -> None:
    path.write_bytes(content)

    with pytest.raises(ChangesFileError):
        load_changes(path, MONDAY)


@pytest.mark.parametrize(
    "stored",
    [
        "skip",
        {key: value for key, value in USABLE.items() if key != "day"},
        {**USABLE, "set_for": "18:00"},
        {**USABLE, "kind": "lunch"},
        {**USABLE, "kind": "instant"},
        {**USABLE, "skipped": "yes"},
        {**USABLE, "time": "25:00"},
        {**USABLE, "text": 5},
        {key: value for key, value in USABLE.items() if key != "name"},
        {**USABLE, "name": ""},
        {**USABLE, "kind": "end", "name": ""},
        {**USABLE, "name": 3},
        {**USABLE, "kind": "custom"},
    ],
    ids=[
        "not an object",
        "no day",
        "no day in the moment",
        "unknown kind",
        "instant",
        "skipped not a flag",
        "no such time",
        "text not a text",
        "start without a name",
        "start with an empty name",
        "end with an empty name",
        "name not a text",
        "custom with a name",
    ],
)
def test_unusable_change_is_left_out(path: Path, stored: object) -> None:
    path.write_text(json.dumps({"changes": [stored, {**USABLE, "skipped": True}]}))

    assert load_changes(path, MONDAY) == (SKIPPED,)
