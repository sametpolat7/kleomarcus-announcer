import datetime
from collections.abc import Callable
from pathlib import Path

from announcer.app import App, ChangeProblem, LastAnnouncement
from announcer.loader import (
    MissingColumnsError,
    UnreadableFileError,
    WrongFileTypeError,
)
from announcer.models import ALL_WEEKDAYS, DELAYS, SPEEDS, CustomAnnouncement, Kind
from announcer.timetable import Conflict, SlotHolder
from announcer.today import Row, Status
from announcer.ui import texts
from support import ROWS, FakeClock, FakeProvider, at, wait_for, write_schedule

WATER = CustomAnnouncement("Su içmeyi unutmayın.", datetime.time(19, 0))
WELCOME = "Ayşe Yılmaz, antrenmanınız başlamıştır. İyi antrenmanlar dileriz."
GOODBYE = "Ayşe Yılmaz, antrenmanınız sona ermiştir. Görüşmek üzere."


def test_every_status_has_a_word_of_its_own() -> None:
    words = list(texts.STATUS.values())

    assert set(texts.STATUS) == set(Status)
    assert len(set(words)) == len(words)


def test_words_of_the_statuses_that_came_with_the_buttons() -> None:
    assert [
        texts.STATUS[status]
        for status in (
            Status.PREPARING,
            Status.QUEUED,
            Status.PLAYING,
            Status.SKIPPING,
            Status.SKIPPED,
            Status.STOPPED,
        )
    ] == ["Hazırlanıyor", "Sırada", "Okunuyor", "Atlanacak", "Atlandı", "Durduruldu"]


def test_every_kind_has_a_word() -> None:
    assert set(texts.KINDS) == set(Kind)
    assert [texts.KINDS[kind] for kind in Kind] == [
        "Bitiş",
        "Başlangıç",
        "Özel",
        "Anlık",
    ]


def test_every_problem_of_a_change_has_a_message() -> None:
    assert set(texts.CHANGE_PROBLEMS) == set(ChangeProblem)


def test_every_kind_that_can_be_edited_has_a_heading() -> None:
    assert [texts.ANNOUNCEMENT_OF[kind] for kind in Kind if kind != Kind.INSTANT] == [
        "Bitiş anonsu",
        "Başlangıç anonsu",
        "Özel anons",
    ]


def test_edit_of_a_start_says_that_the_athletes_end_stays() -> None:
    assert texts.end_stays(datetime.time(19, 2)) == (
        "Bu sporcunun 19:02 bitiş anonsu bu değişiklikten etkilenmez; "
        "gerekirse onu da ayrıca düzenleyin."
    )


def test_status_of_a_row_says_when_the_operator_edited_it() -> None:
    planned = Row(datetime.time(18, 0), Kind.START, WELCOME, Status.NEXT)
    edited = Row(datetime.time(18, 5), Kind.START, WELCOME, Status.NEXT, edited=True)
    skipping = Row(
        datetime.time(19, 0), Kind.END, GOODBYE, Status.SKIPPING, edited=True
    )

    assert texts.status(planned) == "Sıradaki"
    assert texts.status(edited) == "Sıradaki (düzenlendi)"
    assert texts.status(skipping) == "Atlanacak (düzenlendi)"


def test_words_of_the_tables() -> None:
    assert texts.days(ALL_WEEKDAYS) == "Her gün"
    assert texts.days({3, 1}) == "Salı, Perşembe"


def test_words_of_the_settings() -> None:
    assert [texts.speed(speed) for speed in SPEEDS] == [
        "0.8×",
        "0.9×",
        "1.0× (Normal)",
        "1.1×",
        "1.2×",
    ]
    assert [texts.delay(minutes) for minutes in DELAYS] == [
        "Yok",
        "1 dk",
        "2 dk",
        "3 dk",
        "5 dk",
        "10 dk",
    ]
    assert [texts.switch(on) for on in (True, False)] == ["Açık", "Kapalı"]


def test_last_announcement_names_the_athletes_of_each_kind_together() -> None:
    single = LastAnnouncement(at(18, 1), ((Kind.START, "Ayşe Yılmaz"),), Status.PLAYED)
    pair = LastAnnouncement(
        at(18, 1),
        ((Kind.START, "Ayşe Yılmaz"), (Kind.START, "Mehmet Demir")),
        Status.PLAYED,
    )
    several = LastAnnouncement(
        at(19, 1),
        (
            (Kind.END, "Ayşe Yılmaz"),
            (Kind.END, "Mehmet Demir"),
            (Kind.START, "Elif Şahin"),
            (Kind.CUSTOM, "Su içmeyi unutmayın."),
        ),
        Status.STOPPED,
    )

    assert texts.last_announcement(None) == "Son anons: —"
    assert texts.last_announcement(single) == (
        "Son anons: 18:01 · Okundu · Başlangıç: Ayşe Yılmaz"
    )
    assert texts.last_announcement(pair) == (
        "Son anons: 18:01 · Okundu · Başlangıç: Ayşe Yılmaz, Mehmet Demir"
    )
    assert texts.last_announcement(several) == (
        "Son anons: 19:01 · Durduruldu · Bitiş: Ayşe Yılmaz, Mehmet Demir · "
        "Başlangıç: Elif Şahin · Özel: Su içmeyi unutmayın."
    )


def test_last_announcement_of_a_minute_read_athlete_by_athlete(
    app: App, schedule: Path, clock: FakeClock
) -> None:
    app.choose_schedule(schedule)
    wait_for(app, lambda: not app.generating)
    app.start()

    clock.now = at(18, 0, 1)
    wait_for(app, lambda: app.last_announcement is not None)

    assert texts.last_announcement(app.last_announcement) == (
        "Son anons: 18:00 · Okundu · Başlangıç: Ayşe Yılmaz, Mehmet Demir"
    )


def test_activity() -> None:
    assert texts.activity(generating=False, test=None) == ""
    assert texts.activity(generating=True, test=None) == "Sesler oluşturuluyor…"
    assert texts.activity(generating=False, test=Status.PLAYED) == (
        "Test anonsu okundu."
    )
    assert texts.activity(generating=True, test=Status.NO_SPEECH).startswith(
        "Sesler oluşturuluyor…  ·  Test anonsunun sesi oluşturulamadı."
    )
    assert texts.activity(generating=False, test=Status.ASLEEP) == (
        "Atlandı (uyku modu)"
    )


def test_activity_follows_a_test_announcement_until_it_is_stopped() -> None:
    assert [
        texts.activity(generating=False, test=status)
        for status in (
            Status.PREPARING,
            Status.QUEUED,
            Status.PLAYING,
            Status.STOPPED,
        )
    ] == [
        "Test anonsu hazırlanıyor…",
        "Test anonsu sırada…",
        "Test anonsu okunuyor…",
        "Test anonsu durduruldu.",
    ]


def test_schedule_error() -> None:
    missing = MissingColumnsError(("Gün", "Saat"))

    assert texts.schedule_error(missing) == (
        "Sporcu listesinde şu sütunlar eksik: Gün, Saat."
    )
    assert "okunamadı" in texts.schedule_error(UnreadableFileError("no file"))
    assert "yüklenemez" in texts.schedule_error(WrongFileTypeError("a text file"))


def test_conflict_alert_names_each_day_and_what_holds_it() -> None:
    conflicts = (
        Conflict(WATER, 0, SlotHolder.ATHLETES),
        Conflict(WATER, 2, SlotHolder.CUSTOM_ANNOUNCEMENT),
    )

    assert texts.conflict_alert(conflicts) == (
        "Bu saat dolu olduğu için anons kaydedilemedi:\n"
        "• Pazartesi 19:00: sporcu anonsu var\n"
        "• Çarşamba 19:00: başka bir özel anons var"
    )


def test_warnings_cover_everything_that_went_wrong(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    provider.offline = True
    app.choose_schedule(schedule)
    app.save_announcement(WATER)
    write_schedule(
        schedule,
        [*ROWS, ("Elif Şahin", "Pazartesi", "19:00"), ("", "Funday", "18:00")],
    )
    app.refresh()
    wait_for(app, lambda: not app.generating)

    assert texts.warnings(app) == [
        "Sporcu listesinde 6. satır atlandı: ad soyad boş, gün tanınmadı.",
        "“Su içmeyi unutmayın.” özel anonsu Pazartesi 19:00 için atlanıyor: "
        "aynı saatte sporcu anonsu var.",
        "9 anonsun sesi oluşturulamadı. İnternet bağlantısını kontrol edin; "
        "birkaç dakikada bir yeniden denenir.",
    ]


def test_warning_for_a_schedule_that_can_no_longer_be_read(
    app: App, schedule: Path
) -> None:
    app.choose_schedule(schedule)
    wait_for(app, lambda: not app.generating)
    schedule.write_text("no longer a workbook")

    app.refresh()

    assert texts.warnings(app) == [
        "Sporcu listesi okunamadı. Geçerli bir Excel (.xlsx) dosyası olmalıdır. "
        "Son yüklenen liste kullanılıyor."
    ]


def test_warnings_for_a_settings_file_with_problems(
    make_app: Callable[[], App], settings_file: Path
) -> None:
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text(
        '{"custom_announcements": [{"text": "Duyuru", "time": "25:00"}]}',
        encoding="utf-8",
    )
    dropped = make_app()
    settings_file.write_text("{ not json", encoding="utf-8")
    unreadable = make_app()

    assert texts.warnings(dropped)[0] == (
        "Ayarlar dosyasındaki 1 özel anons geçersiz olduğu için yok sayıldı. "
        "Dosyanın bir kopyası “config.json.backup” adıyla saklandı."
    )
    assert texts.warnings(unreadable)[0] == (
        "Ayarlar dosyası okunamadı; varsayılan ayarlar kullanılıyor. "
        "Dosyanın bir kopyası “config.json.backup” adıyla saklandı."
    )


def test_warning_for_a_settings_file_that_could_not_be_copied(
    make_app: Callable[[], App], settings_file: Path
) -> None:
    settings_file.mkdir(parents=True)

    assert texts.warnings(make_app())[0] == (
        "Ayarlar dosyası okunamadı; varsayılan ayarlar kullanılıyor. "
        "Dosya yedeklenemedi; kaydedilen ilk değişiklik onun üzerine yazılır."
    )


def test_warning_for_changes_of_today_that_can_no_longer_be_read(
    make_app: Callable[[], App], settings_file: Path
) -> None:
    settings_file.parent.mkdir(parents=True)
    settings_file.with_name("changes.json").write_text("{ not json", encoding="utf-8")

    app = make_app()

    assert texts.warnings(app) == [
        "Bugün için yapılan değişiklikler okunamadı; anonslar plana göre okunuyor."
        " Dosyanın bir kopyası “changes.json.backup” adıyla saklandı.",
        "Sporcu listesi seçilmedi. “Dosya Seç” ile bir Excel dosyası seçin.",
    ]
    assert (
        settings_file.with_name("changes.json.backup").read_text(encoding="utf-8")
        == "{ not json"
    )


def test_warning_while_a_new_template_has_no_speech(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    wait_for(app, lambda: not app.generating)
    provider.offline = True

    app.set_templates("Hoş geldiniz, {ad_soyad}.", app.settings.end_template)
    wait_for(app, lambda: not app.generating)

    assert texts.warnings(app) == [
        "3 anonsun sesi oluşturulamadı. İnternet bağlantısını kontrol edin; "
        "birkaç dakikada bir yeniden denenir.",
        "Yeni şablonun sesleri hazır olana kadar sporcular önceki şablonla "
        "anons ediliyor.",
    ]


def test_warning_while_a_new_end_template_has_no_speech(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    wait_for(app, lambda: not app.generating)
    provider.offline = True

    app.set_templates(app.settings.template, "Güle güle, {ad_soyad}.")
    wait_for(app, lambda: not app.generating)

    assert texts.warnings(app) == [
        "3 anonsun sesi oluşturulamadı. İnternet bağlantısını kontrol edin; "
        "birkaç dakikada bir yeniden denenir.",
        "Yeni şablonun sesleri hazır olana kadar sporcular önceki şablonla "
        "anons ediliyor.",
    ]


def test_warning_while_a_new_speed_has_no_speech(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    wait_for(app, lambda: not app.generating)
    provider.offline = True

    app.set_speed(0.9)
    wait_for(app, lambda: not app.generating)

    assert texts.warnings(app) == [
        "6 anonsun sesi oluşturulamadı. İnternet bağlantısını kontrol edin; "
        "birkaç dakikada bir yeniden denenir.",
        "Yeni ses hızının sesleri hazır olana kadar anonslar önceki hızla okunuyor.",
    ]
