"""The app behind the window: what it knows and what the operator can do.

``App`` owns the settings, the schedule and the background threads. The
window calls its methods and reads its attributes, all on the main thread.
The background threads never touch that state: they put what happened on a
queue, and ``poll`` takes it in on the main thread.
"""

import dataclasses
import datetime
import logging
import queue
import shutil
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from announcer.loader import (
    LoadedSchedule,
    ScheduleFileError,
    SkippedRow,
    load_schedule,
)
from announcer.models import (
    ALL_WEEKDAYS,
    WEEKDAY_NAMES,
    AthleteEntry,
    CustomAnnouncement,
)
from announcer.player import AudioOutput, Playback, Player
from announcer.scheduler import Scheduler
from announcer.settings import (
    LoadedSettings,
    Settings,
    SettingsFileError,
    load_settings,
    save_settings,
)
from announcer.template import PLACEHOLDER, is_valid_template, render
from announcer.timetable import (
    Conflict,
    Slot,
    Timetable,
    build_timetable,
    find_conflicts,
)
from announcer.today import Row, Status, outcome_status, summary, todays_rows
from announcer.tts import AudioCache, TtsError, TtsProvider

TEST_NAME = "Ayşe Yılmaz"  # a made-up athlete for the test announcement

_WATCH = datetime.timedelta(seconds=2)  # between two looks at the schedule file
_RETRY = datetime.timedelta(minutes=5)  # before missing speech is tried again

_log = logging.getLogger(__name__)


class SlotTakenError(Exception):
    """A custom announcement cannot be saved: its slot is taken on some days."""

    def __init__(self, conflicts: tuple[Conflict, ...]) -> None:
        super().__init__("The slot is already taken")
        self.conflicts = conflicts


@dataclass(frozen=True)
class LastAnnouncement:
    """The announcement that came due most recently, and how it fared."""

    due: datetime.datetime
    summary: str  # the athletes' names, or the custom announcement's text
    status: Status


@dataclass(frozen=True)
class _Due:
    """A slot came due and was handed to the player."""

    due: datetime.datetime
    slot: Slot


@dataclass(frozen=True)
class _Missed:
    """A slot's minute went by while the computer was asleep."""

    due: datetime.datetime
    slot: Slot


@dataclass(frozen=True)
class _Generated:
    """A round of speech generation ended."""

    template: str  # the template that the round was for
    unspoken: tuple[str, ...]  # the texts that still have no speech


class _TestUnspoken:
    """The speech for the test announcement could not be generated."""


type _Event = _Due | _Missed | Playback | _Generated | _TestUnspoken

# What tells a file from an earlier version of itself: when it was changed and
# its size, or None if there is no such file.
type _FileState = tuple[int, int] | None


class App:
    """The state of the app and the actions on it.

    Call ``start`` once, ``poll`` regularly and ``stop`` at the end. The
    public attributes are for reading only.
    """

    def __init__(
        self,
        settings_file: Path,
        cache_directory: Path,
        provider: TtsProvider,
        output: AudioOutput,
        clock: Callable[[], datetime.datetime] = datetime.datetime.now,
        interval: float = 1.0,
    ) -> None:
        self.settings_file = settings_file
        self._clock = clock
        self._events: queue.SimpleQueue[_Event] = queue.SimpleQueue()

        self.settings_unreadable = False  # the defaults are in use instead
        self.settings_backed_up = False  # a copy of a file with problems was kept
        loaded = self._load_settings()
        self.settings: Settings = loaded.settings
        self.dropped_announcements: int = loaded.dropped
        # The template that athletes are announced with. It follows the one in
        # the settings as soon as there is speech for every athlete with it.
        self.spoken_template: str = self.settings.template

        self._entries: tuple[AthleteEntry, ...] = ()
        self.skipped_rows: tuple[SkippedRow, ...] = ()
        self.schedule_error: ScheduleFileError | None = None  # of the last reading
        self.refreshed_at: datetime.datetime | None = None  # the last good reading
        self.timetable = Timetable()
        # What the schedule file looked like when it was last read, and when it
        # was last looked at; a file that was saved since is read again.
        self._read_file: _FileState = None
        self._seen_file: _FileState = None
        self._watched_at = clock()

        self.generating = False  # speech is being generated right now
        self.unspoken: tuple[str, ...] = ()  # the texts that have no speech
        self._regenerate = False  # the texts changed while generating
        self._generated_at = clock()  # when speech was last asked for

        self.last_announcement: LastAnnouncement | None = None
        self.test_status: Status | None = None  # of the latest test announcement
        self._outcomes: dict[tuple[datetime.datetime, str], Status] = {}
        self._summaries: dict[datetime.datetime, str] = {}  # of the slots playing

        self._cache = AudioCache(cache_directory, provider, self.settings.voice)
        self._scheduler = Scheduler(
            self.timetable, self._on_due, self._on_missed, clock, interval
        )
        self._player = Player(self._cache.find, output, self._on_played)

    def start(self) -> None:
        """Load the schedule and start announcing."""
        _log.info("Started; the settings are in %s", self.settings_file)
        self._scheduler.start()
        self._player.start()
        self.refresh()

    def stop(self) -> None:
        """Stop announcing, cutting off what is sounding."""
        self._scheduler.stop()
        self._player.stop()
        _log.info("Stopped")

    def refresh(self) -> None:
        """Read the schedule file again and generate the speech that is missing.

        If the file cannot be read, the schedule that was read last stays in
        use and ``schedule_error`` says why.
        """
        path = self.settings.schedule_path
        if path is not None:
            self._read_file = self._seen_file = _file_state(path)
            try:
                self._use(path, load_schedule(path))
            except ScheduleFileError as error:
                _log.error("Cannot load the schedule: %s (%r)", error, error.__cause__)
                self.schedule_error = error
        self._rebuild()

    def choose_schedule(self, path: Path) -> None:
        """Use the schedule file at ``path`` from now on.

        Raises ``ScheduleFileError`` if it cannot be used and ``OSError`` if
        the choice cannot be saved; nothing changes then.
        """
        state = _file_state(path)
        loaded = load_schedule(path)
        self._save(dataclasses.replace(self.settings, schedule_path=path))
        self._read_file = self._seen_file = state
        self._use(path, loaded)
        self._rebuild()

    def set_template(self, template: str) -> None:
        """Announce athletes with ``template`` once its speech is generated.

        Until then, and for as long as the speech cannot be generated, the
        athletes are announced with the template before it. Raises
        ``ValueError`` if the template has no place for the name and
        ``OSError`` if it cannot be saved; nothing changes then.
        """
        self._save(dataclasses.replace(self.settings, template=template))
        _log.info('Template changed to "%s"', template)
        self._rebuild()

    def save_announcement(
        self, announcement: CustomAnnouncement, replacing: int | None = None
    ) -> None:
        """Add a custom announcement, or put it in place of the one at an index.

        Raises ``SlotTakenError`` if it would share a slot with athletes or
        with another custom announcement, and ``OSError`` if it cannot be
        saved; nothing changes then.
        """
        announcements = list(self.settings.custom_announcements)
        others = [
            item for index, item in enumerate(announcements) if index != replacing
        ]
        conflicts = find_conflicts(announcement, self._entries, others)
        if conflicts:
            raise SlotTakenError(conflicts)
        if replacing is None:
            announcements.append(announcement)
        else:
            announcements[replacing] = announcement
        self._save_announcements(announcements)
        _log.info(
            "Custom announcement %s: %s",
            "added" if replacing is None else "changed",
            _described(announcement),
        )
        self._rebuild()

    def delete_announcement(self, index: int) -> None:
        """Delete the custom announcement at ``index``.

        Raises ``OSError`` if that cannot be saved; nothing changes then.
        """
        announcements = list(self.settings.custom_announcements)
        deleted = announcements.pop(index)
        self._save_announcements(announcements)
        _log.info("Custom announcement deleted: %s", _described(deleted))
        self._rebuild()

    def test(self, template: str) -> None:
        """Announce ``template`` with a made-up name, after what is queued.

        The speech is generated first if it is not stored, which needs a
        network connection. Raises ``ValueError`` if the template has no place
        for the name.
        """
        if not is_valid_template(template):
            raise ValueError(f"The template has no {PLACEHOLDER} placeholder")
        self.preview(render(template, TEST_NAME))

    def preview(self, text: str) -> None:
        """Announce ``text`` once as a test, after what is queued.

        The speech is generated first if it is not stored, which needs a
        network connection. ``test_status`` tells how it went.
        """
        _log.info('Test announcement requested: "%s"', text)
        self.test_status = Status.PLAYING
        threading.Thread(
            target=self._test, args=(text,), name="test", daemon=True
        ).start()

    def rows(self) -> tuple[Row, ...]:
        """Return today's announcements and how each one stands."""
        return todays_rows(self.timetable, self._clock(), self._outcomes)

    def poll(self) -> bool:
        """Take in what the background threads reported since the last call.

        Now and then this also reads a schedule file again that was saved in
        the meantime, and asks again for speech that could not be generated.
        Returns whether anything changed.
        """
        changed = False
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return self._keep_up() or changed
            changed = True
            match event:
                case _Due(due, slot):
                    # Outcomes of earlier days are of no use to today's preview.
                    self._outcomes = {
                        key: status
                        for key, status in self._outcomes.items()
                        if key[0].date() == due.date()
                    }
                    self._outcomes[due, summary(slot)] = Status.PLAYING
                    self._summaries[due] = summary(slot)
                case _Missed(due, slot):
                    self._outcomes[due, summary(slot)] = Status.ASLEEP
                case Playback(due=None):
                    self.test_status = outcome_status(event)
                case Playback(due=due) if due is not None:
                    status = outcome_status(event)
                    announced = self._summaries.pop(due, "")
                    self._outcomes[due, announced] = status
                    self.last_announcement = LastAnnouncement(due, announced, status)
                case _Generated(template, unspoken):
                    self.generating = False
                    self.unspoken = unspoken
                    if not unspoken:
                        self._speak_with(template)
                    if self._regenerate:
                        self._generate()
                case _TestUnspoken():
                    self.test_status = Status.NO_SPEECH

    def _keep_up(self) -> bool:
        """Follow the schedule file and the missing speech; return whether
        anything changed."""
        now = self._clock()
        if abs(now - self._watched_at) < _WATCH:
            return False
        self._watched_at = now
        if (
            self.unspoken
            and not self.generating
            and abs(now - self._generated_at) >= _RETRY
        ):
            _log.info("Trying again to generate the missing speech")
            self._generate()
        path = self.settings.schedule_path
        if path is None:
            return False
        state = _file_state(path)
        if state == self._read_file or state != self._seen_file:
            # Unchanged, or still being written: wait until it has settled.
            self._seen_file = state
            return False
        _log.info("The schedule file was changed; reading it again")
        self.refresh()
        return True

    def _load_settings(self) -> LoadedSettings:
        """Load the settings, or the defaults if the file cannot be read.

        A file that cannot be read, or that holds something unusable, is
        copied first: the next save writes the file anew without that.
        """
        path = self.settings_file
        try:
            loaded = load_settings(path)
        except SettingsFileError:
            _log.exception("Cannot read the settings; starting with the defaults")
            self.settings_unreadable = True
            loaded = LoadedSettings(Settings())
        if loaded.dropped:
            _log.warning(
                "%d unusable custom announcement(s) in %s left out",
                loaded.dropped,
                path,
            )
        if self.settings_unreadable or loaded.dropped:
            try:
                shutil.copyfile(path, backup_copy(path))
                self.settings_backed_up = True
            except OSError:
                _log.exception("Cannot keep a copy of the settings file")
        return loaded

    def _save(self, settings: Settings) -> None:
        """Store ``settings`` and use them from now on."""
        save_settings(settings, self.settings_file)
        self.settings = settings

    def _save_announcements(self, announcements: Iterable[CustomAnnouncement]) -> None:
        self._save(
            dataclasses.replace(
                self.settings, custom_announcements=tuple(announcements)
            )
        )

    def _use(self, path: Path, loaded: LoadedSchedule) -> None:
        """Take over a schedule that was just read from ``path``."""
        self._entries, self.skipped_rows = loaded.entries, loaded.skipped
        self.schedule_error = None
        self.refreshed_at = self._clock()
        _log.info(
            "Schedule loaded from %s: %d session(s), %d row(s) skipped",
            path,
            len(loaded.entries),
            len(loaded.skipped),
        )
        for skipped in loaded.skipped:
            problems = ", ".join(problem.value for problem in skipped.problems)
            _log.warning("Row %d of the schedule skipped: %s", skipped.row, problems)

    def _rebuild(self) -> None:
        """Build the timetable anew and generate the speech that is missing."""
        self._build()
        self._generate()

    def _build(self) -> None:
        """Build the timetable anew and announce by it from now on."""
        known = self.timetable.conflicts
        self.timetable = build_timetable(
            self._entries, self.settings.custom_announcements, self.spoken_template
        )
        for conflict in self.timetable.conflicts:
            if conflict not in known:
                _log.warning(
                    'Custom announcement "%s" is skipped on %s at %s: '
                    "the slot is taken by %s",
                    conflict.announcement.text,
                    WEEKDAY_NAMES[conflict.weekday],
                    f"{conflict.announcement.time:%H:%M}",
                    conflict.holder.value,
                )
        self._scheduler.set_timetable(self.timetable)

    def _speak_with(self, template: str) -> None:
        """Announce athletes with ``template``, now that its speech is there."""
        if template not in (self.spoken_template, self.settings.template):
            return  # the speech is for a template that was replaced meanwhile
        if template != self.spoken_template:
            _log.info('Athletes are announced with "%s" from now on', template)
            self.spoken_template = template
            self._build()

    def _generate(self) -> None:
        """Generate the speech that is not stored yet, on a background thread.

        The speech is that of the template in the settings, which is not
        announced with before all of it is there.
        """
        if self.generating:
            self._regenerate = True  # once more when the running round ends
            return
        self._regenerate = False
        self._generated_at = self._clock()
        template = self.settings.template
        wanted = build_timetable(
            self._entries, self.settings.custom_announcements, template
        )
        texts = tuple(text for slot in wanted.slots for text in slot.texts)
        todo = sum(self._cache.find(text) is None for text in dict.fromkeys(texts))
        if not todo:
            self.unspoken = ()
            self._speak_with(template)
            return
        _log.info("Generating speech for %d sentence(s)", todo)
        self.generating = True
        threading.Thread(
            target=self._generate_all,
            args=(template, texts),
            name="speech",
            daemon=True,
        ).start()

    def _generate_all(self, template: str, texts: tuple[str, ...]) -> None:
        """Generate the speech for ``texts`` and report what is left; on a thread."""
        unspoken = texts
        try:
            unspoken = self._cache.ensure_all(texts)
            _log.info("Speech generated; %d sentence(s) without speech", len(unspoken))
        except Exception:
            _log.exception("Generating speech failed")
        finally:
            self._events.put(_Generated(template, unspoken))

    def _test(self, text: str) -> None:
        """Generate the speech of the test announcement and queue it; on a thread."""
        try:
            self._cache.ensure(text)
        except TtsError as error:
            _log.warning("No speech for the test: %s (%r)", error, error.__cause__)
        except Exception:
            _log.exception("Generating the speech for the test failed")
        else:
            self._player.enqueue((text,))
            return
        self._events.put(_TestUnspoken())

    def _on_due(self, date: datetime.date, slot: Slot) -> None:
        """Hand a due slot to the player; called on the scheduler's thread."""
        due = datetime.datetime.combine(date, slot.time)
        _log.info("%s due: %s", f"{due:%H:%M}", _quoted(slot.texts))
        self._events.put(_Due(due, slot))
        self._player.enqueue(slot.texts, due)

    def _on_missed(self, date: datetime.date, slot: Slot) -> None:
        """Note a slot that was slept through; called on the scheduler's thread."""
        due = datetime.datetime.combine(date, slot.time)
        _log.warning(
            "%s skipped, the computer was asleep: %s",
            f"{due:%Y-%m-%d %H:%M}",
            _quoted(slot.texts),
        )
        self._events.put(_Missed(due, slot))

    def _on_played(self, playback: Playback) -> None:
        """Note how an announcement went; called on the player's thread."""
        name = "Test" if playback.due is None else f"{playback.due:%H:%M}"
        skipped = {*playback.missing, *playback.late}
        played = tuple(text for text in playback.texts if text not in skipped)
        if playback.failed:
            _log.error(
                "%s failed, the audio output could not play all of: %s",
                name,
                _quoted(played),
            )
        elif played:
            _log.info("%s announced: %s", name, _quoted(played))
        if playback.missing:
            _log.warning(
                "%s skipped, no stored speech: %s", name, _quoted(playback.missing)
            )
        if playback.late:
            _log.warning(
                "%s skipped, the computer was asleep: %s", name, _quoted(playback.late)
            )
        self._events.put(playback)


def _file_state(path: Path) -> _FileState:
    """Return what tells a file from an earlier version of itself."""
    try:
        status = path.stat()
    except OSError:
        return None
    return status.st_mtime_ns, status.st_size


def backup_copy(settings_file: Path) -> Path:
    """Return where a copy of a settings file with problems is kept."""
    return settings_file.with_name(f"{settings_file.name}.backup")


def _quoted(texts: Iterable[str]) -> str:
    """Return ``texts`` for a log line, each in quotes."""
    return ", ".join(f'"{text}"' for text in texts)


def _described(announcement: CustomAnnouncement) -> str:
    """Return a custom announcement for a log line."""
    days = (
        "every day"
        if announcement.weekdays == ALL_WEEKDAYS
        else ", ".join(WEEKDAY_NAMES[day] for day in sorted(announcement.weekdays))
    )
    return f'"{announcement.text}" at {announcement.time:%H:%M} on {days}'
