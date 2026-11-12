"""The window calls ``App``'s methods and reads its attributes, all on the main
thread. The background threads never touch that state: they put what happened
on a queue, and ``poll`` takes it in on the main thread.
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

from announcer import VERSION
from announcer.loader import (
    LoadedSchedule,
    ScheduleFileError,
    SkippedRow,
    load_schedule,
)
from announcer.models import (
    ALL_WEEKDAYS,
    NORMAL_SPEED,
    SPEEDS,
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
    set_for,
)
from announcer.today import Row, Status, outcome_status, summary, todays_rows
from announcer.tts import AudioCache, TtsError, TtsProvider

TEST_NAME = "Ayşe Yılmaz"  # a made-up athlete

_WATCH = 2.0  # seconds between two looks at the schedule file
_REREADS = 3  # how often a changed schedule file that cannot be read is retried
# Before something that failed is tried again: missing speech, or a schedule
# file that could not be read.
_RETRY = datetime.timedelta(minutes=5)
_DAY = datetime.timedelta(days=1)

_log = logging.getLogger(__name__)


class SlotTakenError(Exception):
    def __init__(self, conflicts: tuple[Conflict, ...]) -> None:
        super().__init__("The slot is already taken")
        self.conflicts = conflicts


@dataclass(frozen=True)
class LastAnnouncement:
    due: datetime.datetime
    summary: str  # the athletes' names, or the custom announcement's text
    status: Status


@dataclass(frozen=True)
class _Due:
    due: datetime.datetime
    slot: Slot


@dataclass(frozen=True)
class _Missed:
    due: datetime.datetime
    slot: Slot


@dataclass(frozen=True)
class _Generated:
    template: str
    speed: float
    unspoken: tuple[str, ...]


class _TestUnspoken:
    """The speech for the test announcement could not be generated."""


# The moment that a slot was set for and its summary: what an outcome is of.
type _Key = tuple[datetime.datetime, str]

# When a file was changed and its size, or None if there is no such file.
type _FileState = tuple[int, int] | None
_UNREAD: _FileState = (-1, -1)  # what no file looks like: it is read at a look


@dataclass(frozen=True)
class _FileSeen:
    path: Path
    state: _FileState


type _Event = _Due | _Missed | Playback | _Generated | _TestUnspoken | _FileSeen


class App:
    """Call ``start`` once, ``poll`` regularly and ``stop`` at the end. The
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

        self.settings_unreadable = False
        self.settings_backed_up = False
        loaded = self._load_settings()
        self.settings: Settings = loaded.settings
        self.dropped_announcements: int = loaded.dropped
        # The template that athletes are announced with and the speed that
        # everything is spoken at. They follow the ones in the settings as
        # soon as there is speech for every announcement with them.
        self.spoken_template: str = self.settings.template
        self.spoken_speed: float = self.settings.speed

        self._entries: tuple[AthleteEntry, ...] = ()
        self.skipped_rows: tuple[SkippedRow, ...] = ()
        self.schedule_error: ScheduleFileError | None = None  # of the last reading
        self.refreshed_at: datetime.datetime | None = None  # the last good reading
        self.timetable = Timetable()
        # What the schedule file looked like when it was last read, and when it
        # was last looked at.
        self._read_file: _FileState = None
        self._seen_file: _FileState = None
        self._rereads = 0  # failed readings of the file as it is now
        self._read_at = clock()  # when the file was last read, or tried to be
        self._stopped = threading.Event()

        self.generating = False
        self.unspoken: tuple[str, ...] = ()
        self._regenerate = False  # the texts changed while generating
        self._generated_at = clock()  # when a round of speech last began or ended

        self.last_announcement: LastAnnouncement | None = None
        self.test_status: Status | None = None
        self._outcomes: dict[_Key, Status] = {}
        self._playing: dict[datetime.datetime, _Key] = {}  # by when they were due

        self._cache = AudioCache(cache_directory, provider, self.settings.voice)
        self._scheduler = Scheduler(
            self.timetable, self._on_due, self._on_missed, clock, interval
        )
        self._player = Player(self._cache.find, output, self._on_played)

    def start(self) -> None:
        _log.info(
            "Version %s started; the settings are in %s", VERSION, self.settings_file
        )
        now = self._clock()
        self._scheduler.start()
        self._player.start()
        self.refresh()
        self._keep_a_spoken_speed()
        # The scheduler leaves out the slot of the minute it starts in, which
        # a run that was closed a moment ago may have announced.
        slot = self.timetable.slot_at(
            now.weekday(), datetime.time(now.hour, now.minute)
        )
        if slot is not None:
            due = datetime.datetime.combine(now.date(), slot.time)
            self._outcomes[_key(due, slot)] = Status.PASSED
            _log.warning(
                "%s not announced, its minute had begun when the app started: %s",
                f"{due:%H:%M}",
                _quoted(slot.texts),
            )
        threading.Thread(target=self._watch, name="watch", daemon=True).start()

    def stop(self) -> None:
        self._stopped.set()
        self._scheduler.stop()
        self._player.stop()
        _log.info("Stopped")

    def refresh(self) -> None:
        """If the schedule file cannot be read, the schedule that was read last stays
        in use and ``schedule_error`` says why.
        """
        path = self.settings.schedule_path
        if path is not None:
            self._read_at = self._clock()
            self._read_file = self._seen_file = _file_state(path)
            try:
                self._use(path, load_schedule(path))
            except ScheduleFileError as error:
                _log.error("Cannot load the schedule: %s (%r)", error, error.__cause__)
                self.schedule_error = error
        self._rebuild()

    def choose_schedule(self, path: Path) -> None:
        """Raises ``ScheduleFileError`` if the file cannot be used and ``OSError``
        if the choice cannot be saved; nothing changes then.
        """
        state = _file_state(path)
        loaded = load_schedule(path)
        self._save(dataclasses.replace(self.settings, schedule_path=path))
        self._read_file = self._seen_file = state
        self._use(path, loaded)
        self._rebuild()

    def set_template(self, template: str) -> None:
        """Raises ``ValueError`` if the template has no place for the name and
        ``OSError`` if it cannot be saved; nothing changes then.
        """
        self._save(dataclasses.replace(self.settings, template=template))
        _log.info('Template changed to "%s"', template)
        self._rebuild()

    def set_speed(self, speed: float) -> None:
        """Raises ``ValueError`` if the speed is not one of ``SPEEDS`` and
        ``OSError`` if it cannot be saved; nothing changes then.
        """
        self._save(dataclasses.replace(self.settings, speed=speed))
        _log.info("Speed changed to %s", speed)
        self._generate()

    def set_delay(self, minutes: int) -> None:
        """Raises ``ValueError`` if the delay is not one of ``DELAYS`` and
        ``OSError`` if it cannot be saved; nothing changes then.
        """
        self._save(dataclasses.replace(self.settings, delay=minutes))
        _log.info("Delay changed to %d minute(s)", minutes)
        self._build()

    def save_announcement(
        self, announcement: CustomAnnouncement, replacing: int | None = None
    ) -> None:
        """Raises ``SlotTakenError`` if the announcement would share a slot with
        athletes or with another custom announcement, and ``OSError`` if it
        cannot be saved; nothing changes then.
        """
        announcements = list(self.settings.custom_announcements)
        others = [
            item for index, item in enumerate(announcements) if index != replacing
        ]
        conflicts = find_conflicts(announcement, self._entries, others)
        if conflicts:
            _log.info(
                "Custom announcement not saved, its slot is taken: %s",
                _described(announcement),
            )
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
        """Raises ``OSError`` if the deletion cannot be saved; nothing changes then."""
        announcements = list(self.settings.custom_announcements)
        deleted = announcements.pop(index)
        self._save_announcements(announcements)
        _log.info("Custom announcement deleted: %s", _described(deleted))
        self._rebuild()

    def test(self, template: str) -> None:
        """Raises ``ValueError`` if the template has no place for the name."""
        if not is_valid_template(template):
            raise ValueError(f"The template has no {PLACEHOLDER} placeholder")
        self.preview(render(template, TEST_NAME))

    def preview(self, text: str) -> None:
        """``test_status`` tells how the test went. The text is spoken at the
        speed in the settings, also while the announcements still have the
        one before.
        """
        _log.info('Test announcement requested: "%s"', text)
        self.test_status = Status.PLAYING
        threading.Thread(
            target=self._test,
            args=(text, self.settings.speed),
            name="test",
            daemon=True,
        ).start()

    def rows(self) -> tuple[Row, ...]:
        return todays_rows(self.timetable, self._clock(), self._outcomes)

    def replay(self, row: Row) -> bool:
        """Returns whether the row was announced again: not if it is not among
        today's any more, or is not ``replayable``. The row then tells how
        that went.
        """
        now = self._clock()
        slot = self.timetable.slot_at(now.weekday(), row.time)
        due = datetime.datetime.combine(now.date(), row.time)
        if (
            slot is None
            or not row.replayable
            or row not in todays_rows(self.timetable, now, self._outcomes)
            # What was at that time before the schedule changed is sounding.
            or due in self._playing
        ):
            return False
        _log.info("%s asked for again: %s", f"{due:%H:%M}", _quoted(slot.texts))
        self._outcomes[_key(due, slot)] = Status.PLAYING
        self._playing[due] = _key(due, slot)
        self._player.enqueue(slot.texts, due, slot.speed)
        return True

    def poll(self) -> bool:
        """Returns whether anything changed."""
        changed = False
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                retried = self._retry_schedule()
                return self._retry_speech() or retried or changed
            if isinstance(event, _FileSeen):
                changed = self._follow(event.path, event.state) or changed
                continue
            changed = True
            match event:
                case _Due(due, slot):
                    # Outcomes of earlier days are of no use to today's preview.
                    self._outcomes = {
                        key: status
                        for key, status in self._outcomes.items()
                        if due - key[0] < _DAY
                    }
                    self._outcomes[_key(due, slot)] = Status.PLAYING
                    self._playing[due] = _key(due, slot)
                case _Missed(due, slot):
                    self._outcomes[_key(due, slot)] = Status.ASLEEP
                case Playback(due=None):
                    self.test_status = outcome_status(event)
                case Playback(due=due) if due is not None:
                    status = outcome_status(event)
                    key = self._playing.pop(due, None)
                    if key is not None:
                        self._outcomes[key] = status
                        self.last_announcement = LastAnnouncement(due, key[1], status)
                case _Generated(template, speed, unspoken):
                    self.generating = False
                    self.unspoken = unspoken
                    self._generated_at = self._clock()
                    if not unspoken:
                        self._speak_with(template, speed)
                    if self._regenerate:
                        self._generate()
                case _TestUnspoken():
                    self.test_status = Status.NO_SPEECH

    def _follow(self, path: Path, state: _FileState) -> bool:
        """Return whether the schedule file was read again."""
        if path != self.settings.schedule_path:
            return False  # a look at a file that was replaced meanwhile
        if state == self._read_file or state != self._seen_file:
            # Unchanged, or still being written: wait until it has settled.
            if state != self._seen_file:
                self._rereads = 0
            self._seen_file = state
            return False
        _log.info("The schedule file was changed; reading it again")
        self.refresh()
        if self.schedule_error is not None and self._rereads < _REREADS:
            # Something else may only have had it open for a moment, so the
            # next looks try again, a few times at most.
            self._rereads += 1
            self._read_file = _UNREAD
        return True

    def _retry_schedule(self) -> bool:
        """Return whether the schedule file was read again. There is no button
        for that, and a file on a network drive may turn up without having
        changed."""
        if self.schedule_error is None or abs(self._clock() - self._read_at) < _RETRY:
            return False
        _log.info("Trying again to read the schedule file")
        self.refresh()
        return True

    def _retry_speech(self) -> bool:
        """Return whether speech was asked for again."""
        if (
            not self.unspoken
            or self.generating
            or abs(self._clock() - self._generated_at) < _RETRY
        ):
            return False
        _log.info("Trying again to generate the missing speech")
        self._generate()
        return True

    def _watch(self) -> None:
        """Runs on a thread, as a file on a network drive can take long to answer."""
        while not self._stopped.wait(_WATCH):
            path = self.settings.schedule_path
            if path is not None:
                self._events.put(_FileSeen(path, _file_state(path)))

    def _load_settings(self) -> LoadedSettings:
        """A settings file that cannot be read, or that holds something unusable, is
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
        save_settings(settings, self.settings_file)
        self.settings = settings

    def _save_announcements(self, announcements: Iterable[CustomAnnouncement]) -> None:
        self._save(
            dataclasses.replace(
                self.settings, custom_announcements=tuple(announcements)
            )
        )

    def _use(self, path: Path, loaded: LoadedSchedule) -> None:
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
        self._build()
        self._generate()

    def _build(self) -> None:
        known = self.timetable.conflicts
        self.timetable = build_timetable(
            self._entries,
            self.settings.custom_announcements,
            self.spoken_template,
            self.spoken_speed,
            self.settings.delay,
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

    def _keep_a_spoken_speed(self) -> None:
        """A speed that was chosen shortly before the app was closed may have no
        speech yet. The announcements then start with a speed that has, the
        one nearest to the normal speed, as they would have gone on with the
        one before.
        """

        def spoken(speed: float) -> bool:
            return all(
                self._cache.find(text, speed) is not None
                for slot in self.timetable.slots
                for text in slot.texts
            )

        if spoken(self.spoken_speed):
            return
        for speed in sorted(SPEEDS, key=lambda speed: abs(speed - NORMAL_SPEED)):
            if spoken(speed):
                _log.warning(
                    "No speech yet at speed %s; announcing at %s until there is",
                    self.spoken_speed,
                    speed,
                )
                self.spoken_speed = speed
                self._build()
                return

    def _speak_with(self, template: str, speed: float) -> None:
        """Announce with ``template`` and at ``speed``, now that the speech for
        that is there.
        """
        if (template, speed) != (self.settings.template, self.settings.speed):
            return  # the speech is for settings that were replaced meanwhile
        if (template, speed) != (self.spoken_template, self.spoken_speed):
            _log.info(
                'Athletes are announced with "%s" and everything at speed %s '
                "from now on",
                template,
                speed,
            )
            self.spoken_template, self.spoken_speed = template, speed
            self._build()

    def _generate(self) -> None:
        if self.generating:
            self._regenerate = True  # once more when the running round ends
            return
        self._regenerate = False
        self._generated_at = self._clock()
        template, speed = self.settings.template, self.settings.speed
        wanted = build_timetable(
            self._entries, self.settings.custom_announcements, template
        )
        texts = tuple(text for slot in wanted.slots for text in slot.texts)
        todo = sum(
            self._cache.find(text, speed) is None for text in dict.fromkeys(texts)
        )
        if not todo:
            self.unspoken = ()
            self._speak_with(template, speed)
            return
        _log.info("Generating speech for %d sentence(s)", todo)
        self.generating = True
        threading.Thread(
            target=self._generate_all,
            args=(template, speed, texts),
            name="speech",
            daemon=True,
        ).start()

    def _generate_all(
        self, template: str, speed: float, texts: tuple[str, ...]
    ) -> None:
        """Runs on a thread."""
        unspoken = texts
        try:
            unspoken = self._cache.ensure_all(texts, speed)
            _log.info("Speech generated; %d sentence(s) without speech", len(unspoken))
        except Exception:
            _log.exception("Generating speech failed")
        finally:
            self._events.put(_Generated(template, speed, unspoken))

    def _test(self, text: str, speed: float) -> None:
        """Runs on a thread."""
        try:
            self._cache.ensure(text, speed)
        except TtsError as error:
            _log.warning("No speech for the test: %s (%r)", error, error.__cause__)
        except Exception:
            _log.exception("Generating the speech for the test failed")
        else:
            self._player.enqueue((text,), speed=speed)
            return
        self._events.put(_TestUnspoken())

    def _on_due(self, date: datetime.date, slot: Slot) -> None:
        """Called on the scheduler's thread."""
        due = datetime.datetime.combine(date, slot.time)
        _log.info("%s due: %s", f"{due:%H:%M}", _quoted(slot.texts))
        self._events.put(_Due(due, slot))
        self._player.enqueue(slot.texts, due, slot.speed)

    def _on_missed(self, date: datetime.date, slot: Slot) -> None:
        """Called on the scheduler's thread."""
        due = datetime.datetime.combine(date, slot.time)
        _log.warning(
            "%s skipped, the computer was asleep: %s",
            f"{due:%Y-%m-%d %H:%M}",
            _quoted(slot.texts),
        )
        self._events.put(_Missed(due, slot))

    def _on_played(self, playback: Playback) -> None:
        """Called on the player's thread."""
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


def _key(due: datetime.datetime, slot: Slot) -> _Key:
    return set_for(due.date(), slot), summary(slot)


def _file_state(path: Path) -> _FileState:
    try:
        status = path.stat()
    except OSError:
        return None
    return status.st_mtime_ns, status.st_size


def backup_copy(settings_file: Path) -> Path:
    return settings_file.with_name(f"{settings_file.name}.backup")


def _quoted(texts: Iterable[str]) -> str:
    return ", ".join(f'"{text}"' for text in texts)


def _described(announcement: CustomAnnouncement) -> str:
    days = (
        "every day"
        if announcement.weekdays == ALL_WEEKDAYS
        else ", ".join(WEEKDAY_NAMES[day] for day in sorted(announcement.weekdays))
    )
    return f'"{announcement.text}" at {announcement.time:%H:%M} on {days}'
