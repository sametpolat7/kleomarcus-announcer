import datetime
from collections.abc import Iterable, Mapping

import pytest

from announcer.models import AthleteEntry, Change, CustomAnnouncement, Kind
from announcer.player import Heard, Playback
from announcer.scheduler import LATE
from announcer.timetable import Part, Timetable, build_timetable
from announcer.today import (
    CHANGE_MARGIN,
    Instant,
    OutcomeKey,
    Row,
    Status,
    outcome_status,
    part_status,
    todays_rows,
    who,
)
from support import MONDAY, at

SIX_PM, SEVEN_PM, EIGHT_PM = datetime.time(18), datetime.time(19), datetime.time(20)
DAY = datetime.timedelta(days=1)

AYSE = AthleteEntry("Ayşe Yılmaz", 0, SIX_PM)
MEHMET = AthleteEntry("Mehmet Demir", 0, SIX_PM)
ZEYNEP = AthleteEntry("Zeynep Kaya", 1, SIX_PM)
WATER = CustomAnnouncement("Su içmeyi unutmayın.", SEVEN_PM)
TIMETABLE = build_timetable(
    [AYSE, MEHMET, ZEYNEP], [WATER], "{ad_soyad}, hoş geldiniz."
)

REMINDER = "Su içmeyi unutmayın."

# A Monday evening with the ends of the sessions announced too: Ayşe and
# Mehmet leave as Zeynep arrives, and the hall closes as she leaves.
ZEYNEP_ON_MONDAY = AthleteEntry("Zeynep Kaya", 0, SEVEN_PM)
CLOSING = CustomAnnouncement("Salon kapanıyor.", EIGHT_PM)


def hello(athlete: AthleteEntry) -> str:
    return f"{athlete.name}, hoş geldiniz."


def goodbye(athlete: AthleteEntry) -> str:
    return f"{athlete.name}, görüşmek üzere."


def evening(changes: Iterable[Change] = (), delay: int = 0) -> Timetable:
    return build_timetable(
        [AYSE, MEHMET, ZEYNEP_ON_MONDAY],
        [CLOSING],
        "{ad_soyad}, hoş geldiniz.",
        delay=delay,
        end_template="{ad_soyad}, görüşmek üzere.",
        changes=changes,
    )


def statuses(
    now: datetime.datetime,
    outcomes: Mapping[OutcomeKey, Status] | None = None,
    timetable: Timetable = TIMETABLE,
) -> list[Status]:
    return [row.status for row in todays_rows(timetable, now, outcomes or {})]


def listed(rows: Iterable[Row]) -> list[tuple[datetime.time, Kind, str, Status]]:
    return [(row.time, row.kind, row.text, row.status) for row in rows]


def first(timetable: Timetable, now: datetime.datetime) -> Row:
    return todays_rows(timetable, now, {})[0]


def test_rows_list_todays_slots_in_time_order() -> None:
    assert todays_rows(TIMETABLE, at(8), {}) == (
        Row(
            SIX_PM,
            Kind.START,
            hello(AYSE),
            Status.NEXT,
            (at(18), Kind.START, AYSE.name),
            editable=True,
            skippable=True,
        ),
        Row(
            SIX_PM,
            Kind.START,
            hello(MEHMET),
            Status.NEXT,
            (at(18), Kind.START, MEHMET.name),
            editable=True,
            skippable=True,
        ),
        Row(
            SEVEN_PM,
            Kind.CUSTOM,
            REMINDER,
            Status.WAITING,
            (at(19), Kind.CUSTOM, ""),
            editable=True,
            skippable=True,
        ),
    )


def test_rows_are_those_of_the_weekday_of_now() -> None:
    tuesday = at(8) + DAY

    assert [row.text for row in todays_rows(TIMETABLE, tuesday, {})] == [
        hello(ZEYNEP),
        REMINDER,
    ]
    assert todays_rows(Timetable(), at(8), {}) == ()


def test_each_athlete_of_a_minute_is_a_row_of_its_own_with_its_kind() -> None:
    rows = todays_rows(evening(), at(8), {})

    assert listed(rows) == [
        (SIX_PM, Kind.START, hello(AYSE), Status.NEXT),
        (SIX_PM, Kind.START, hello(MEHMET), Status.NEXT),
        (SEVEN_PM, Kind.END, goodbye(AYSE), Status.WAITING),
        (SEVEN_PM, Kind.END, goodbye(MEHMET), Status.WAITING),
        (SEVEN_PM, Kind.START, hello(ZEYNEP), Status.WAITING),
        (EIGHT_PM, Kind.END, goodbye(ZEYNEP), Status.WAITING),
        (EIGHT_PM, Kind.CUSTOM, "Salon kapanıyor.", Status.WAITING),
    ]
    assert [row.key for row in rows] == [
        (at(18), Kind.START, AYSE.name),
        (at(18), Kind.START, MEHMET.name),
        (at(19), Kind.END, AYSE.name),
        (at(19), Kind.END, MEHMET.name),
        (at(19), Kind.START, ZEYNEP.name),
        (at(20), Kind.END, ZEYNEP.name),
        (at(20), Kind.CUSTOM, ""),
    ]


def test_athletes_of_a_minute_are_listed_in_the_order_of_the_schedule() -> None:
    timetable = build_timetable(
        [MEHMET, ZEYNEP_ON_MONDAY, AYSE],
        [],
        "{ad_soyad}, hoş geldiniz.",
        end_template="{ad_soyad}, görüşmek üzere.",
    )

    assert [(row.kind, row.text) for row in todays_rows(timetable, at(8), {})] == [
        (Kind.START, hello(MEHMET)),
        (Kind.START, hello(AYSE)),
        (Kind.END, goodbye(MEHMET)),
        (Kind.END, goodbye(AYSE)),
        (Kind.START, hello(ZEYNEP)),
        (Kind.END, goodbye(ZEYNEP)),
    ]


def test_athlete_moved_into_a_later_minute_is_read_before_those_set_for_it() -> None:
    moved = Change(MONDAY, at(18), Kind.START, AYSE.name, time=SEVEN_PM)

    rows = todays_rows(evening([moved]), at(8), {})

    assert [(row.time, row.kind, row.text) for row in rows[:5]] == [
        (SIX_PM, Kind.START, hello(MEHMET)),
        (SEVEN_PM, Kind.END, goodbye(AYSE)),
        (SEVEN_PM, Kind.END, goodbye(MEHMET)),
        (SEVEN_PM, Kind.START, hello(AYSE)),
        (SEVEN_PM, Kind.START, hello(ZEYNEP)),
    ]
    assert rows[3].key == (at(18), Kind.START, AYSE.name)


def test_end_of_a_session_is_listed_an_hour_after_its_start_with_the_delay() -> None:
    rows = todays_rows(evening(delay=1), at(8), {})

    assert [(row.time, row.kind) for row in rows[:4]] == [
        (datetime.time(18, 1), Kind.START),
        (datetime.time(18, 1), Kind.START),
        (datetime.time(19, 1), Kind.END),
        (datetime.time(19, 1), Kind.END),
    ]
    assert rows[2].key == (at(19), Kind.END, AYSE.name)


def test_every_part_of_the_first_minute_to_come_is_next() -> None:
    assert statuses(at(18, 30), timetable=evening()) == [
        Status.PASSED,
        Status.PASSED,
        Status.NEXT,
        Status.NEXT,
        Status.NEXT,
        Status.WAITING,
        Status.WAITING,
    ]
    assert statuses(at(19, 30), timetable=evening()) == [
        Status.PASSED,
        Status.PASSED,
        Status.PASSED,
        Status.PASSED,
        Status.PASSED,
        Status.NEXT,
        Status.NEXT,
    ]


def test_athlete_with_back_to_back_sessions_is_listed_at_an_end_and_a_start() -> None:
    again = AthleteEntry("Ayşe Yılmaz", 0, SEVEN_PM)
    timetable = build_timetable(
        [AYSE, again],
        [],
        "{ad_soyad}, hoş geldiniz.",
        end_template="{ad_soyad}, görüşmek üzere.",
    )

    rows = todays_rows(timetable, at(18, 30), {})

    assert listed(rows) == [
        (SIX_PM, Kind.START, hello(AYSE), Status.PASSED),
        (SEVEN_PM, Kind.END, goodbye(AYSE), Status.NEXT),
        (SEVEN_PM, Kind.START, hello(AYSE), Status.NEXT),
        (EIGHT_PM, Kind.END, goodbye(AYSE), Status.WAITING),
    ]
    assert rows[1].key == (at(19), Kind.END, AYSE.name)
    assert rows[2].key == (at(19), Kind.START, AYSE.name)


def test_only_the_first_slot_to_come_is_next() -> None:
    assert statuses(at(17, 59, 59)) == [Status.NEXT, Status.NEXT, Status.WAITING]
    assert statuses(at(18, 1)) == [Status.PASSED, Status.PASSED, Status.NEXT]
    assert statuses(at(23)) == [Status.PASSED] * 3


def test_slot_is_passed_once_it_is_too_late_to_announce_it() -> None:
    assert statuses(at(18, 0, 30)) == [Status.NEXT, Status.NEXT, Status.WAITING]
    assert statuses(at(18, 0, 31)) == [Status.PASSED, Status.PASSED, Status.NEXT]


def test_skipped_part_is_skipping_until_its_time_goes_by_and_never_next() -> None:
    timetable = evening([Change(MONDAY, at(18), Kind.START, AYSE.name, skipped=True)])

    assert statuses(at(17), timetable=timetable)[:3] == [
        Status.SKIPPING,
        Status.NEXT,
        Status.WAITING,
    ]
    assert statuses(at(18, 0, 30), timetable=timetable)[:2] == [
        Status.SKIPPING,
        Status.NEXT,
    ]
    assert statuses(at(18, 0, 31), timetable=timetable)[:2] == [
        Status.SKIPPED,
        Status.PASSED,
    ]
    assert statuses(at(23), timetable=timetable)[0] is Status.SKIPPED


def test_skipped_part_leaves_next_to_the_rest_of_its_minute_or_the_next_one() -> None:
    goodbyes = [
        Change(MONDAY, at(19), Kind.END, name, skipped=True)
        for name in (AYSE.name, MEHMET.name)
    ]
    welcome = Change(MONDAY, at(19), Kind.START, ZEYNEP.name, skipped=True)

    assert statuses(at(18, 30), timetable=evening(goodbyes[:1])) == [
        Status.PASSED,
        Status.PASSED,
        Status.SKIPPING,
        Status.NEXT,
        Status.NEXT,
        Status.WAITING,
        Status.WAITING,
    ]
    assert statuses(at(18, 30), timetable=evening([*goodbyes, welcome])) == [
        Status.PASSED,
        Status.PASSED,
        Status.SKIPPING,
        Status.SKIPPING,
        Status.SKIPPING,
        Status.NEXT,
        Status.NEXT,
    ]


def test_skip_or_edit_of_one_athlete_leaves_the_others_of_the_minute_alone() -> None:
    skip = Change(MONDAY, at(18), Kind.START, AYSE.name, skipped=True)
    edit = Change(MONDAY, at(19), Kind.END, MEHMET.name, text="Yarın görüşürüz.")

    rows = todays_rows(evening([skip, edit]), at(8), {})

    assert [
        (row.time, row.text, row.status, row.edited, row.editable, row.skippable)
        for row in rows[:5]
    ] == [
        (SIX_PM, hello(AYSE), Status.SKIPPING, False, True, True),
        (SIX_PM, hello(MEHMET), Status.NEXT, False, True, True),
        (SEVEN_PM, goodbye(AYSE), Status.WAITING, False, True, True),
        (SEVEN_PM, "Yarın görüşürüz.", Status.WAITING, True, True, True),
        (SEVEN_PM, hello(ZEYNEP), Status.WAITING, False, True, True),
    ]


def test_edited_sentence_says_the_name_typed_into_it_as_a_placeholder() -> None:
    edit = Change(
        MONDAY, at(18), Kind.START, MEHMET.name, text="Tekrar hoş geldin {ad_soyad}."
    )

    rows = todays_rows(evening([edit]), at(8), {})

    assert [(row.text, row.edited) for row in rows[:2]] == [
        (hello(AYSE), False),
        ("Tekrar hoş geldin Mehmet Demir.", True),
    ]
    assert rows[1].key == (at(18), Kind.START, MEHMET.name)


def test_rows_can_be_changed_and_skipped_until_shortly_before_their_minute() -> None:
    too_late = at(18) - CHANGE_MARGIN
    in_time = too_late - datetime.timedelta(seconds=1)

    row = first(evening(), in_time)
    assert (row.status, row.editable, row.skippable) == (Status.NEXT, True, True)
    row = first(evening(), too_late)
    assert (row.status, row.editable, row.skippable) == (Status.NEXT, False, False)
    row = first(evening(), at(18, 0, 10))
    assert (row.status, row.editable, row.skippable) == (Status.NEXT, False, False)
    later = todays_rows(evening(), too_late, {})[2]
    assert (later.status, later.editable, later.skippable) == (
        Status.WAITING,
        True,
        True,
    )


def test_passed_known_and_conflicting_rows_can_neither_be_changed_nor_skipped() -> None:
    reminder = CustomAnnouncement("Salon kapanıyor.", SIX_PM)
    # The delay went up after the reminder was queued, so its minute is ahead.
    timetable = build_timetable([AYSE], [reminder, WATER], "{ad_soyad}", delay=10)
    outcomes: dict[OutcomeKey, Status] = {
        (at(19), Kind.CUSTOM, REMINDER): Status.QUEUED,
    }

    rows = todays_rows(timetable, at(19, 0, 1), outcomes)

    assert listed(rows) == [
        (datetime.time(18, 10), Kind.START, "Ayşe Yılmaz", Status.PASSED),
        (datetime.time(18, 10), Kind.CUSTOM, "Salon kapanıyor.", Status.CONFLICT),
        (datetime.time(19, 10), Kind.CUSTOM, REMINDER, Status.QUEUED),
    ]
    assert [(row.editable, row.skippable) for row in rows] == [(False, False)] * 3
    assert rows[1].key is None
    unknown = todays_rows(timetable, at(19, 0, 1), {})[2]
    assert (unknown.status, unknown.editable, unknown.skippable) == (
        Status.NEXT,
        True,
        True,
    )


@pytest.mark.parametrize(
    "outcome",
    [
        Status.QUEUED,
        Status.PLAYING,
        Status.PLAYED,
        Status.PARTLY_PLAYED,
        Status.NO_SPEECH,
        Status.ASLEEP,
        Status.NO_OUTPUT,
        Status.STOPPED,
    ],
)
def test_row_with_an_outcome_is_fixed_though_the_delay_put_its_minute_ahead(
    outcome: Status,
) -> None:
    known: dict[OutcomeKey, Status] = {
        (at(18), Kind.START, AYSE.name): outcome,
        (at(18), Kind.START, MEHMET.name): outcome,
    }
    skip = Change(MONDAY, at(18), Kind.START, AYSE.name, skipped=True)

    for timetable in (evening(delay=10), evening([skip], delay=10)):
        rows = todays_rows(timetable, at(18, 5), known)

        assert [
            (row.time, row.kind, row.status, row.editable, row.skippable)
            for row in rows[:5]
        ] == [
            (datetime.time(18, 10), Kind.START, outcome, False, False),
            (datetime.time(18, 10), Kind.START, outcome, False, False),
            (datetime.time(19, 10), Kind.END, Status.NEXT, True, True),
            (datetime.time(19, 10), Kind.END, Status.NEXT, True, True),
            (datetime.time(19, 10), Kind.START, Status.NEXT, True, True),
        ]


def test_skipped_row_can_be_changed_before_its_minute() -> None:
    timetable = evening([Change(MONDAY, at(18), Kind.START, AYSE.name, skipped=True)])

    row = first(timetable, at(17))
    assert (row.status, row.editable, row.skippable) == (Status.SKIPPING, True, True)
    row = first(timetable, at(18) - CHANGE_MARGIN)
    assert (row.status, row.editable) == (Status.SKIPPING, False)


def test_skip_can_be_undone_until_shortly_before_it_is_too_late_to_announce() -> None:
    timetable = evening([Change(MONDAY, at(18), Kind.START, AYSE.name, skipped=True)])
    last_chance = at(18) + LATE - CHANGE_MARGIN

    row = first(timetable, last_chance)
    assert (row.status, row.skippable) == (Status.SKIPPING, True)
    row = first(timetable, last_chance + datetime.timedelta(seconds=1))
    assert (row.status, row.skippable) == (Status.SKIPPING, False)
    row = first(timetable, at(18) + LATE + datetime.timedelta(seconds=1))
    assert (row.status, row.skippable) == (Status.SKIPPED, False)


def test_what_happened_to_a_skipped_part_beats_its_skip() -> None:
    timetable = evening([Change(MONDAY, at(18), Kind.START, AYSE.name, skipped=True)])
    key = (at(18), Kind.START, AYSE.name)

    row = todays_rows(timetable, at(18, 0, 10), {key: Status.PLAYING})[0]
    assert (row.status, row.editable, row.skippable) == (Status.PLAYING, False, False)
    row = todays_rows(timetable, at(19), {key: Status.PLAYED})[0]
    assert (row.status, row.skippable) == (Status.PLAYED, False)


def test_row_tells_whether_the_operator_changed_it_for_today() -> None:
    timetable = evening(
        [
            Change(MONDAY, at(18), Kind.START, AYSE.name, time=datetime.time(18, 5)),
            Change(MONDAY, at(19), Kind.END, MEHMET.name, text="Yarın görüşürüz."),
            Change(MONDAY, at(20), Kind.CUSTOM, skipped=True),
            Change(MONDAY + DAY, at(19) + DAY, Kind.START, ZEYNEP.name, skipped=True),
        ]
    )

    rows = todays_rows(timetable, at(8), {})

    assert [(row.time, row.kind, row.text, row.edited) for row in rows] == [
        (SIX_PM, Kind.START, hello(MEHMET), False),
        (datetime.time(18, 5), Kind.START, hello(AYSE), True),
        (SEVEN_PM, Kind.END, goodbye(AYSE), False),
        (SEVEN_PM, Kind.END, "Yarın görüşürüz.", True),
        (SEVEN_PM, Kind.START, hello(ZEYNEP), False),
        (EIGHT_PM, Kind.END, goodbye(ZEYNEP), False),
        (EIGHT_PM, Kind.CUSTOM, "Salon kapanıyor.", False),
    ]
    assert rows[1].key == (at(18), Kind.START, AYSE.name)
    assert rows[6].status is Status.SKIPPING


def test_outcome_of_a_moved_part_goes_by_the_moment_it_was_set_for() -> None:
    moved = Change(MONDAY, at(18), Kind.START, AYSE.name, time=datetime.time(18, 5))
    outcomes: dict[OutcomeKey, Status] = {
        (at(18), Kind.START, AYSE.name): Status.PLAYED
    }

    assert listed(todays_rows(evening([moved]), at(18, 6), outcomes)[:2]) == [
        (SIX_PM, Kind.START, hello(MEHMET), Status.PASSED),
        (datetime.time(18, 5), Kind.START, hello(AYSE), Status.PLAYED),
    ]


def test_known_outcome_is_shown_instead_of_the_clock() -> None:
    outcomes: dict[OutcomeKey, Status] = {
        (at(18), Kind.START, AYSE.name): Status.PLAYED,
        (at(18), Kind.START, MEHMET.name): Status.PLAYED,
        (at(19), Kind.CUSTOM, REMINDER): Status.PLAYING,
    }

    assert statuses(at(19, 0, 2), outcomes) == [
        Status.PLAYED,
        Status.PLAYED,
        Status.PLAYING,
    ]
    assert statuses(at(17), {(at(18), Kind.START, AYSE.name): Status.ASLEEP}) == [
        Status.ASLEEP,
        Status.NEXT,
        Status.WAITING,
    ]


def test_each_athlete_of_a_minute_shows_what_happened_to_them() -> None:
    outcomes: dict[OutcomeKey, Status] = {
        (at(18), Kind.START, AYSE.name): Status.PLAYED,
        (at(18), Kind.START, MEHMET.name): Status.PLAYING,
    }

    assert statuses(at(18, 0, 5), outcomes) == [
        Status.PLAYED,
        Status.PLAYING,
        Status.NEXT,
    ]


def test_outcome_stays_with_the_athlete_when_the_template_changes() -> None:
    outcomes: dict[OutcomeKey, Status] = {
        (at(18), Kind.START, AYSE.name): Status.PLAYED,
        (at(19), Kind.CUSTOM, REMINDER): Status.PLAYED,
    }
    changed = build_timetable([AYSE, MEHMET, ZEYNEP], [WATER], "Hoş geldin {ad_soyad}.")

    assert listed(todays_rows(changed, at(19, 30), outcomes)) == [
        (SIX_PM, Kind.START, "Hoş geldin Ayşe Yılmaz.", Status.PLAYED),
        (SIX_PM, Kind.START, "Hoş geldin Mehmet Demir.", Status.PASSED),
        (SEVEN_PM, Kind.CUSTOM, REMINDER, Status.PLAYED),
    ]


def test_outcome_of_another_day_kind_athlete_or_announcement_does_not_count() -> None:
    yesterday = at(18) - DAY
    outcomes: dict[OutcomeKey, Status] = {
        (yesterday, Kind.START, AYSE.name): Status.PLAYED,
        (at(18), Kind.END, AYSE.name): Status.PLAYED,
        (at(18), Kind.START, ZEYNEP.name): Status.PLAYED,
        # An outcome goes by whom it is for, not by what was read.
        (at(18), Kind.START, hello(MEHMET)): Status.PLAYED,
        (at(19), Kind.CUSTOM, "Salon kapanıyor."): Status.PLAYED,
    }

    assert statuses(at(17), outcomes) == [Status.NEXT, Status.NEXT, Status.WAITING]
    assert statuses(at(19, 30), outcomes) == [Status.PASSED] * 3


def test_part_is_for_its_athlete_or_for_the_text_of_its_announcement() -> None:
    assert who(Part(Kind.START, hello(AYSE), AYSE.name)) == AYSE.name
    assert who(Part(Kind.END, "Görüşmek üzere.", MEHMET.name)) == MEHMET.name
    assert who(Part(Kind.CUSTOM, REMINDER)) == REMINDER


def test_custom_announcement_that_lost_todays_slot_is_listed_after_the_winner() -> None:
    reminder = CustomAnnouncement("Salon kapanıyor.", SIX_PM, frozenset({0, 2}))
    timetable = build_timetable([AYSE], [reminder], "{ad_soyad}")

    rows = todays_rows(timetable, at(8), {})
    assert listed(rows) == [
        (SIX_PM, Kind.START, "Ayşe Yılmaz", Status.NEXT),
        (SIX_PM, Kind.CUSTOM, "Salon kapanıyor.", Status.CONFLICT),
    ]
    assert rows[1] == Row(SIX_PM, Kind.CUSTOM, "Salon kapanıyor.", Status.CONFLICT)
    wednesday = at(8) + 2 * DAY
    assert listed(todays_rows(timetable, wednesday, {})) == [
        (SIX_PM, Kind.CUSTOM, "Salon kapanıyor.", Status.NEXT),
    ]


def test_custom_announcement_in_a_minute_where_sessions_only_end_is_kept() -> None:
    reminder = CustomAnnouncement("Salon kapanıyor.", SEVEN_PM, frozenset({0}))
    timetable = build_timetable(
        [AYSE, MEHMET],
        [reminder],
        "{ad_soyad}, hoş geldiniz.",
        end_template="{ad_soyad}, görüşmek üzere.",
    )

    assert listed(todays_rows(timetable, at(18, 30), {}))[2:] == [
        (SEVEN_PM, Kind.END, goodbye(AYSE), Status.NEXT),
        (SEVEN_PM, Kind.END, goodbye(MEHMET), Status.NEXT),
        (SEVEN_PM, Kind.CUSTOM, "Salon kapanıyor.", Status.NEXT),
    ]


def test_rows_show_when_a_delayed_announcement_is_made() -> None:
    reminder = CustomAnnouncement("Salon kapanıyor.", SIX_PM, frozenset({0}))
    late = CustomAnnouncement("Salon kapandı.", datetime.time(23, 58), frozenset({6}))
    timetable = build_timetable([AYSE], [reminder, late], "{ad_soyad}", delay=3)
    three_past = datetime.time(18, 3)

    rows = todays_rows(timetable, at(18, 2), {})
    assert listed(rows) == [
        # From Sunday night, and the scheduler was not there for it.
        (datetime.time(0, 1), Kind.CUSTOM, "Salon kapandı.", Status.PASSED),
        (three_past, Kind.START, "Ayşe Yılmaz", Status.NEXT),
        (three_past, Kind.CUSTOM, "Salon kapanıyor.", Status.CONFLICT),
    ]
    assert rows[0].key == (at(23, 58) - DAY, Kind.CUSTOM, "")
    # An outcome goes by the moment that its part was set for.
    outcomes: dict[OutcomeKey, Status] = {
        (at(18), Kind.START, "Ayşe Yılmaz"): Status.PLAYED
    }
    assert todays_rows(timetable, at(18, 4), outcomes)[1].status is Status.PLAYED
    at_another_delay = build_timetable([AYSE], [], "{ad_soyad}", delay=10)
    assert listed(todays_rows(at_another_delay, at(18, 4), outcomes)) == [
        (datetime.time(18, 10), Kind.START, "Ayşe Yılmaz", Status.PLAYED),
    ]


def test_what_lost_its_slot_on_the_day_before_midnight_is_listed_after_it() -> None:
    late = AthleteEntry("Ayşe Yılmaz", 6, datetime.time(23, 58))
    reminder = CustomAnnouncement("Salon kapandı.", late.time, frozenset({6}))
    timetable = build_timetable([late], [reminder], "{ad_soyad}", delay=5)

    assert [row.status for row in todays_rows(timetable, at(0, 0), {})] == [
        Status.NEXT,
        Status.CONFLICT,
    ]
    sunday = at(8) + 6 * DAY
    assert todays_rows(timetable, sunday, {}) == ()


def test_instant_announcements_of_today_are_listed_when_they_were_asked_for() -> None:
    instant = Instant(at(18, 30, 15), "Salon on dakika sonra kapanacak.")
    yesterdays = Instant(at(18, 30) - DAY, "Dünkü anons.")

    rows = todays_rows(evening(), at(18, 31), {}, [instant, yesterdays])

    assert len(rows) == 8
    assert rows[2] == Row(
        datetime.time(18, 30, 15),
        Kind.INSTANT,
        "Salon on dakika sonra kapanacak.",
        Status.PREPARING,
        instant,
    )
    assert rows[3].kind is Kind.END


def test_instant_announcement_shows_what_happened_to_it() -> None:
    instant = Instant(at(18, 30, 15), "Salon on dakika sonra kapanacak.")

    for status in (Status.QUEUED, Status.PLAYING, Status.STOPPED):
        rows = todays_rows(evening(), at(18, 31), {instant.key: status}, [instant])
        assert rows[2].status is status
    assert instant.key == (at(18, 30, 15), Kind.INSTANT, instant.text)


REPLAYABLE = {
    Status.WAITING: False,
    Status.NEXT: False,
    Status.PASSED: True,
    Status.PREPARING: False,
    Status.QUEUED: False,
    Status.PLAYING: False,
    Status.PLAYED: True,
    Status.PARTLY_PLAYED: True,
    Status.NO_SPEECH: True,
    Status.ASLEEP: True,
    Status.NO_OUTPUT: True,
    Status.CONFLICT: False,
    Status.SKIPPING: False,
    Status.SKIPPED: True,
    Status.STOPPED: True,
}


@pytest.mark.parametrize("status", list(Status))
def test_only_an_announcement_whose_time_has_come_can_be_replayed(
    status: Status,
) -> None:
    row = Row(SIX_PM, Kind.START, hello(AYSE), status)

    assert row.replayable is REPLAYABLE[status]


@pytest.mark.parametrize("status", list(Status))
def test_row_whose_minute_is_still_being_read_cannot_be_replayed(
    status: Status,
) -> None:
    row = Row(SIX_PM, Kind.START, hello(AYSE), status, in_group=True)

    assert not row.replayable


def test_rows_of_what_is_queued_or_being_read_are_in_their_group() -> None:
    instant = Instant(at(19, 59, 50), "Salon on dakika sonra kapanacak.")
    earlier = Instant(at(19, 30), "Salon temizleniyor.")
    playing = {
        (at(20), Kind.END, ZEYNEP_ON_MONDAY.name),
        (at(20), Kind.CUSTOM, "Salon kapanıyor."),
        instant.key,
    }
    outcomes: dict[OutcomeKey, Status] = {
        (at(18), Kind.START, AYSE.name): Status.PLAYED,
        (at(20), Kind.END, ZEYNEP_ON_MONDAY.name): Status.PLAYED,
        (at(20), Kind.CUSTOM, "Salon kapanıyor."): Status.PLAYING,
        instant.key: Status.QUEUED,
        earlier.key: Status.PLAYED,
    }

    rows = todays_rows(evening(), at(20, 0, 5), outcomes, [earlier, instant], playing)

    assert [
        (row.kind, row.text, row.status, row.in_group, row.replayable) for row in rows
    ] == [
        (Kind.START, hello(AYSE), Status.PLAYED, False, True),
        (Kind.START, hello(MEHMET), Status.PASSED, False, True),
        (Kind.END, goodbye(AYSE), Status.PASSED, False, True),
        (Kind.END, goodbye(MEHMET), Status.PASSED, False, True),
        (Kind.START, hello(ZEYNEP), Status.PASSED, False, True),
        (Kind.INSTANT, earlier.text, Status.PLAYED, False, True),
        (Kind.INSTANT, instant.text, Status.QUEUED, True, False),
        (Kind.END, goodbye(ZEYNEP), Status.PLAYED, True, False),
        (Kind.CUSTOM, "Salon kapanıyor.", Status.PLAYING, True, False),
    ]


@pytest.mark.parametrize(
    ("playback", "status"),
    [
        (Playback(("a", "b"), (), (), False), Status.PLAYED),
        (Playback(("a", "b"), ("a",), (), False), Status.PARTLY_PLAYED),
        (Playback(("a", "b"), (), ("b",), False), Status.PARTLY_PLAYED),
        (Playback(("a", "b"), ("a", "b"), (), False), Status.NO_SPEECH),
        (Playback(("a", "b"), (), ("a", "b"), False), Status.ASLEEP),
        (Playback(("a", "b"), ("a",), ("b",), False), Status.ASLEEP),
        (Playback(("a", "b"), ("a",), (), True), Status.NO_OUTPUT),
        (Playback(("a", "b"), (), (), False, cut=("b",)), Status.STOPPED),
        (Playback(("a", "b"), ("a",), (), False, cut=("b",)), Status.STOPPED),
        (Playback(("a", "b"), (), (), True, cut=("b",)), Status.NO_OUTPUT),
    ],
)
def test_outcome_status(playback: Playback, status: Status) -> None:
    assert outcome_status(playback) is status


def played(*parts: tuple[str, Heard]) -> Playback:
    """A playback of the texts with what became of each, as the player makes it."""
    texts = tuple(text for text, _ in parts)

    def of(what: Heard) -> tuple[str, ...]:
        return tuple(text for text, was in parts if was is what)

    return Playback(
        texts,
        of(Heard.NO_SPEECH),
        of(Heard.LATE),
        bool(of(Heard.UNPLAYABLE)),
        cut=of(Heard.CUT),
        heard=tuple(was for _, was in parts),
    )


@pytest.mark.parametrize(
    ("heard", "status"),
    [
        (Heard.YES, Status.PLAYED),
        (Heard.NO_SPEECH, Status.NO_SPEECH),
        (Heard.UNPLAYABLE, Status.NO_OUTPUT),
        (Heard.LATE, Status.ASLEEP),
        (Heard.CUT, Status.STOPPED),
    ],
)
def test_part_status_is_what_became_of_the_text_at_the_place_of_the_part(
    heard: Heard, status: Status
) -> None:
    playback = played((hello(AYSE), Heard.YES), (hello(MEHMET), heard))

    assert part_status(playback, 1) is status
    assert part_status(playback, 0) is Status.PLAYED


def test_part_status_tells_each_part_of_a_playback_by_its_place() -> None:
    playback = played(
        ("end", Heard.YES),
        ("start 1", Heard.YES),
        ("start 2", Heard.NO_SPEECH),
        ("custom", Heard.CUT),
        ("other", Heard.CUT),
    )

    assert [part_status(playback, index) for index in range(5)] == [
        Status.PLAYED,
        Status.PLAYED,
        Status.NO_SPEECH,
        Status.STOPPED,
        Status.STOPPED,
    ]


def test_part_status_tells_parts_that_read_the_same_apart_by_their_place() -> None:
    playback = played(("Hoş geldiniz.", Heard.YES), ("Hoş geldiniz.", Heard.CUT))

    assert part_status(playback, 0) is Status.PLAYED
    assert part_status(playback, 1) is Status.STOPPED


def test_part_status_of_a_failed_playback_is_no_output_only_where_it_failed() -> None:
    one_file = played(("end", Heard.YES), ("start", Heard.UNPLAYABLE))
    no_device = played(
        ("end", Heard.UNPLAYABLE),
        ("start 1", Heard.NO_SPEECH),
        ("start 2", Heard.UNPLAYABLE),
    )

    assert one_file.failed and no_device.failed
    assert part_status(one_file, 0) is Status.PLAYED
    assert part_status(one_file, 1) is Status.NO_OUTPUT
    assert [part_status(no_device, index) for index in range(3)] == [
        Status.NO_OUTPUT,
        Status.NO_SPEECH,
        Status.NO_OUTPUT,
    ]
