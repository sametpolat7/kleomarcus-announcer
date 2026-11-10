"""Tests for the Turkish wording of the window."""

import datetime
from collections.abc import Callable
from pathlib import Path

from announcer.app import App
from announcer.loader import (
    MissingColumnsError,
    UnreadableFileError,
    WrongFileTypeError,
)
from announcer.models import ALL_WEEKDAYS, CustomAnnouncement
from announcer.timetable import Conflict, SlotHolder
from announcer.today import Status
from announcer.ui import texts
from support import ROWS, FakeProvider, wait_for, write_schedule

WATER = CustomAnnouncement("Su içmeyi unutmayın.", datetime.time(19, 0))


def test_every_status_has_a_word() -> None:
    assert set(texts.STATUS) == set(Status)


def test_words_of_the_tables() -> None:
    assert texts.days(ALL_WEEKDAYS) == "Her gün"
    assert texts.days({3, 1}) == "Salı, Perşembe"


def test_activity() -> None:
    assert texts.activity(generating=False, test=None) == ""
    assert texts.activity(generating=True, test=None) == "Sesler oluşturuluyor…"
    assert texts.activity(generating=False, test=Status.PLAYED) == (
        "Test anonsu okundu."
    )
    assert texts.activity(generating=True, test=Status.NO_SPEECH).startswith(
        "Sesler oluşturuluyor…  ·  Test anonsunun sesi oluşturulamadı."
    )
    # A status without wording of its own falls back to the word of the table.
    assert texts.activity(generating=False, test=Status.ASLEEP) == (
        "Atlandı (uyku modu)"
    )


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
        "5 anonsun sesi oluşturulamadı. İnternet bağlantısını kontrol edin; "
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


def test_warning_while_a_new_template_has_no_speech(
    app: App, schedule: Path, provider: FakeProvider
) -> None:
    app.choose_schedule(schedule)
    wait_for(app, lambda: not app.generating)
    provider.offline = True

    app.set_template("Hoş geldiniz, {ad_soyad}.")
    wait_for(app, lambda: not app.generating)

    assert texts.warnings(app) == [
        "3 anonsun sesi oluşturulamadı. İnternet bağlantısını kontrol edin; "
        "birkaç dakikada bir yeniden denenir.",
        "Yeni şablonun sesleri hazır olana kadar sporcular önceki şablonla "
        "anons ediliyor.",
    ]
