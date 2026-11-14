"""Nothing here touches tkinter, so the wording can be tested without a display."""

import datetime
from collections.abc import Iterable

from announcer.app import App, ChangeProblem, LastAnnouncement, backup_copy
from announcer.loader import (
    MissingColumnsError,
    RowProblem,
    ScheduleFileError,
    WrongFileTypeError,
)
from announcer.models import ALL_WEEKDAYS, NORMAL_SPEED, SESSION, WEEKDAY_NAMES, Kind
from announcer.template import PLACEHOLDER
from announcer.timetable import Conflict, SlotHolder
from announcer.today import Row, Status

SLEEP_NOTICE = (
    "Uyku modunda anonslar seslendirilmez. Bilgisayarınızı ve uygulamayı açık tutunuz."
)
NO_ROWS = "Bugün için anons yok."
NO_ANNOUNCEMENTS = "Özel anons yok."
CLOSE_QUESTION = "Uygulama kapatılırsa anonslar okunmaz. Kapatılsın mı?"
ALREADY_RUNNING = "Uygulama zaten çalışıyor."
TEMPLATE_HINT = (
    f"“{PLACEHOLDER}” yerine sporcunun adı soyadı okunur. "
    "Yeri değişebilir, yazımı değişmemelidir."
)
END_HINT = (
    f"Bitiş anonsu, antrenman başladıktan {SESSION.seconds // 60} dakika sonra okunur."
)
TODAY_ONLY = (
    "Değişiklik yalnızca bugün için geçerlidir; "
    "sporcu listesi ve özel anonslar değişmez."
)
INSTANT_HINT = "Metin hemen okunur; o sırada okunan bir anons varsa onun ardından."
TEMPLATE_NEEDS_PLACEHOLDER = f"Şablonda “{PLACEHOLDER}” yer tutucusu bulunmalıdır."
START_NEEDS_PLACEHOLDER = (
    f"Başlangıç şablonunda “{PLACEHOLDER}” yer tutucusu bulunmalıdır."
)
END_NEEDS_PLACEHOLDER = f"Bitiş şablonunda “{PLACEHOLDER}” yer tutucusu bulunmalıdır."
SKIP = "Atla"
UNSKIP = "Atlamayı Geri Al"
EDITED = "(düzenlendi)"
TIME_NOT_DELAYED = "Burada seçilen saate gecikme süresi eklenmez."
SPEECH_PREPARING = "Yeni metnin sesi hazırlanıyor…"
CANNOT_SAVE = "Değişiklik kaydedilemedi. Ayrıntılar kayıt dosyasındadır."

STATUS: dict[Status, str] = {
    Status.WAITING: "Bekliyor",
    Status.NEXT: "Sıradaki",
    Status.PASSED: "Saati geçti",
    Status.PREPARING: "Hazırlanıyor",
    Status.QUEUED: "Sırada",
    Status.PLAYING: "Okunuyor",
    Status.PLAYED: "Okundu",
    Status.PARTLY_PLAYED: "Eksik okundu",
    Status.NO_SPEECH: "Atlandı (ses yok)",
    Status.ASLEEP: "Atlandı (uyku modu)",
    Status.NO_OUTPUT: "Hata (ses çıkışı)",
    Status.CONFLICT: "Atlandı (çakışma)",
    Status.SKIPPING: "Atlanacak",
    Status.SKIPPED: "Atlandı",
    Status.STOPPED: "Durduruldu",
}

# What the edit dialog calls the announcement it changes.
ANNOUNCEMENT_OF: dict[Kind, str] = {
    Kind.START: "Başlangıç anonsu",
    Kind.END: "Bitiş anonsu",
    Kind.CUSTOM: "Özel anons",
}

KINDS: dict[Kind, str] = {
    Kind.END: "Bitiş",
    Kind.START: "Başlangıç",
    Kind.CUSTOM: "Özel",
    Kind.INSTANT: "Anlık",
}

CHANGE_PROBLEMS: dict[ChangeProblem, str] = {
    ChangeProblem.GONE: "Bu anonsun saati geldi; artık değiştirilemez.",
    ChangeProblem.NO_SPEECH: (
        "Yeni metnin sesi oluşturulamadı. İnternet bağlantısını kontrol edin. "
        "Saat, metin plana döndürülerek yine de değiştirilebilir."
    ),
    ChangeProblem.PAST: "Bu saat geçti. Şu andan sonraki bir saat seçin.",
    ChangeProblem.NO_TEXT: "Anons metni boş olamaz.",
}

_TEST_STATUS: dict[Status, str] = {
    Status.PREPARING: "Test anonsu hazırlanıyor…",
    Status.QUEUED: "Test anonsu sırada…",
    Status.PLAYING: "Test anonsu okunuyor…",
    Status.PLAYED: "Test anonsu okundu.",
    Status.STOPPED: "Test anonsu durduruldu.",
    Status.NO_SPEECH: (
        "Test anonsunun sesi oluşturulamadı. İnternet bağlantısını kontrol edin."
    ),
    Status.NO_OUTPUT: "Test anonsu okunamadı. Ses çıkışını kontrol edin.",
}

_ROW_PROBLEMS: dict[RowProblem, str] = {
    RowProblem.MISSING_NAME: "ad soyad boş",
    RowProblem.UNKNOWN_DAY: "gün tanınmadı",
    RowProblem.INVALID_TIME: "saat geçersiz",
}

_HOLDERS: dict[SlotHolder, str] = {
    SlotHolder.ATHLETES: "sporcu anonsu",
    SlotHolder.CUSTOM_ANNOUNCEMENT: "başka bir özel anons",
}

_MONTHS = (
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
)


def days(weekdays: Iterable[int]) -> str:
    chosen = sorted(weekdays)
    if chosen == sorted(ALL_WEEKDAYS):
        return "Her gün"
    return ", ".join(WEEKDAY_NAMES[day] for day in chosen)


def speed(value: float) -> str:
    return f"{value:.1f}×" + (" (Normal)" if value == NORMAL_SPEED else "")


def delay(minutes: int) -> str:
    return f"{minutes} dk" if minutes else "Yok"


def end_stays(end: datetime.time) -> str:
    return (
        f"Bu sporcunun {end:%H:%M} bitiş anonsu bu değişiklikten etkilenmez; "
        "gerekirse onu da ayrıca düzenleyin."
    )


def switch(on: bool) -> str:
    return "Açık" if on else "Kapalı"


def status(row: Row) -> str:
    return STATUS[row.status] + (f" {EDITED}" if row.edited else "")


def today_title(now: datetime.datetime) -> str:
    month, weekday = _MONTHS[now.month - 1], WEEKDAY_NAMES[now.weekday()]
    return f"{now.day} {month} {now.year} {weekday}"


def last_loaded(moment: datetime.datetime | None) -> str:
    when = f"{moment:%d.%m.%Y %H:%M:%S}" if moment else "—"
    return f"Son Yüklenme Tarihi: {when}"


def last_announcement(last: LastAnnouncement | None) -> str:
    if last is None:
        return "Son anons: —"
    # The names of each kind together, the kinds in the order they were read.
    said: dict[Kind, list[str]] = {}
    for kind, who in last.parts:
        said.setdefault(kind, []).append(who)
    parts = " · ".join(
        f"{KINDS[kind]}: {', '.join(whom)}" for kind, whom in said.items()
    )
    # The status before the parts, as a long text may not fit the window.
    return f"Son anons: {last.due:%H:%M} · {STATUS[last.status]} · {parts}"


def activity(generating: bool, test: Status | None) -> str:
    parts = [
        "Sesler oluşturuluyor…" if generating else "",
        "" if test is None else _TEST_STATUS.get(test, STATUS[test]),
    ]
    return "  ·  ".join(part for part in parts if part)


def schedule_error(error: ScheduleFileError) -> str:
    if isinstance(error, MissingColumnsError):
        return f"Sporcu listesinde şu sütunlar eksik: {', '.join(error.columns)}."
    if isinstance(error, WrongFileTypeError):
        return (
            "Bu dosya yüklenemez. Sporcu listesi bir Excel (.xlsx) dosyası olmalıdır."
        )
    return "Sporcu listesi okunamadı. Geçerli bir Excel (.xlsx) dosyası olmalıdır."


def conflict_alert(conflicts: Iterable[Conflict]) -> str:
    lines = [
        f"• {WEEKDAY_NAMES[conflict.weekday]} {conflict.announcement.time:%H:%M}: "
        f"{_HOLDERS[conflict.holder]} var"
        for conflict in conflicts
    ]
    return "\n".join(["Bu saat dolu olduğu için anons kaydedilemedi:", *lines])


def warnings(app: App) -> list[str]:
    """The most basic warning comes first."""
    lines: list[str] = []
    copy = backup_copy(app.settings_file).name
    kept = (
        f" Dosyanın bir kopyası “{copy}” adıyla saklandı."
        if app.settings_backed_up
        else " Dosya yedeklenemedi; kaydedilen ilk değişiklik onun üzerine yazılır."
    )
    if app.settings_unreadable:
        lines.append(
            "Ayarlar dosyası okunamadı; varsayılan ayarlar kullanılıyor." + kept
        )
    if app.dropped_announcements:
        lines.append(
            f"Ayarlar dosyasındaki {app.dropped_announcements} özel anons "
            "geçersiz olduğu için yok sayıldı." + kept
        )
    if app.changes_unreadable:
        changes_copy = backup_copy(app.changes_file).name
        lines.append(
            "Bugün için yapılan değişiklikler okunamadı; anonslar plana göre okunuyor."
            + (
                f" Dosyanın bir kopyası “{changes_copy}” adıyla saklandı."
                if app.changes_backed_up
                else ""
            )
        )
    if app.settings.schedule_path is None:
        lines.append(
            "Sporcu listesi seçilmedi. “Dosya Seç” ile bir Excel dosyası seçin."
        )
    if app.schedule_error is not None:
        last = " Son yüklenen liste kullanılıyor." if app.refreshed_at else ""
        lines.append(schedule_error(app.schedule_error) + last)
    for skipped in app.skipped_rows:
        problems = ", ".join(_ROW_PROBLEMS[problem] for problem in skipped.problems)
        lines.append(f"Sporcu listesinde {skipped.row}. satır atlandı: {problems}.")
    for conflict in app.timetable.conflicts:
        announcement = conflict.announcement
        lines.append(
            f"“{announcement.text}” özel anonsu {WEEKDAY_NAMES[conflict.weekday]} "
            f"{announcement.time:%H:%M} için atlanıyor: aynı saatte "
            f"{_HOLDERS[conflict.holder]} var."
        )
    if app.unspoken:
        lines.append(
            f"{len(app.unspoken)} anonsun sesi oluşturulamadı. İnternet bağlantısını "
            "kontrol edin; birkaç dakikada bir yeniden denenir."
        )
        if (app.spoken_template, app.spoken_end_template) != (
            app.settings.template,
            app.settings.end_template,
        ):
            lines.append(
                "Yeni şablonun sesleri hazır olana kadar sporcular önceki şablonla "
                "anons ediliyor."
            )
        if app.spoken_speed != app.settings.speed:
            lines.append(
                "Yeni ses hızının sesleri hazır olana kadar anonslar önceki hızla "
                "okunuyor."
            )
    return lines
