"""The window calls ``App``'s methods and reads its attributes, all on the main
thread. The background threads never touch that state: they put what happened
on a queue, and ``poll`` takes it in on the main thread.
"""

import dataclasses
import datetime
import itertools
import logging
import queue
import shutil
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from announcer import VERSION
from announcer.changes import ChangesFileError, load_changes, save_changes
from announcer.loader import (
    LoadedSchedule,
    ScheduleFileError,
    SkippedRow,
    load_schedule,
)
from announcer.models import (
    ALL_WEEKDAYS,
    NORMAL_SPEED,
    SESSION,
    SPEEDS,
    WEEKDAY_NAMES,
    AthleteEntry,
    Change,
    CustomAnnouncement,
    Kind,
)
from announcer.player import AudioOutput, Playback, Player
from announcer.scheduler import LATE, Scheduler, untaken
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
    Part,
    PartId,
    Slot,
    Timetable,
    build_timetable,
    find_conflicts,
    part_id,
    said,
    set_for,
)
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
from announcer.tts import AudioCache, TtsProvider

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


class ChangeProblem(Enum):
    GONE = "gone"  # the row's time has come, or is about to
    PAST = "past"  # the time chosen for it has already come
    NO_TEXT = "no text"
    NO_SPEECH = "no speech"  # for the new text: see ``make_speech``


class ChangeError(Exception):
    def __init__(self, problem: ChangeProblem) -> None:
        super().__init__(f"The change cannot be made: {problem.value}")
        self.problem = problem


@dataclass(frozen=True)
class Editing:
    kind: Kind
    planned: datetime.time  # when the row is announced without today's change
    time: datetime.time  # when it is announced with it
    # What it says without the change: for an athlete, the template filled in
    # with the name; for a custom announcement, its text. And what it says
    # with the change.
    planned_text: str
    text: str
    name: str  # the athlete's; empty for a custom announcement
    # For the start of a session: when its end is announced today, if that is
    # still to come.
    end: datetime.time | None = None


@dataclass
class SpeechRequest:
    """``poll`` fills in ``done`` and ``spoken``."""

    texts: tuple[str, ...]
    done: bool = False
    spoken: bool = False


@dataclass(frozen=True)
class LastAnnouncement:
    due: datetime.datetime
    parts: tuple[tuple[Kind, str], ...]  # each kind with whom it was for
    status: Status


@dataclass(frozen=True)
class _Group:
    """What is queued in the player in one go, after one chime."""

    number: int  # one of its own, as the same part can be queued again
    due: datetime.datetime
    parts: tuple[tuple[OutcomeKey, str], ...]  # with what is said


@dataclass(frozen=True)
class _Test:
    number: int


@dataclass(frozen=True)
class _Due:
    group: _Group


@dataclass(frozen=True)
class _Started:
    """The player began to play what it was given with this tag."""

    tag: object


@dataclass(frozen=True)
class _Speaking:
    """The player began to say the text at ``index`` of what it was given with
    this tag."""

    tag: object
    index: int


@dataclass(frozen=True)
class _Missed:
    date: datetime.date
    slot: Slot


@dataclass(frozen=True)
class _Generated:
    templates: tuple[str, str]  # for the start and the end of a session
    speed: float
    unspoken: tuple[str, ...]


# What speech is made for at once, one after the other, and read when it is
# there: an instant announcement or a test, with its text and speed. Or what
# is only made ahead of time, at each of its speeds.
type _Wanted = (
    tuple[Instant | _Test, str, float] | tuple[SpeechRequest, tuple[float, ...]]
)


@dataclass(frozen=True)
class _Spoken:
    """The speech asked for is there, or could not be made."""

    wanted: _Wanted
    spoken: bool


# When a file was changed and its size, or None if there is no such file.
type _FileState = tuple[int, int] | None
_UNREAD: _FileState = (-1, -1)  # what no file looks like: it is read at a look


@dataclass(frozen=True)
class _FileSeen:
    path: Path
    state: _FileState


type _Event = (
    _Due | _Missed | _Started | _Speaking | Playback | _Generated | _Spoken | _FileSeen
)


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
        # The spoken templates and speed follow the ones in the settings as soon as
        # there is speech for every announcement with them.
        self.spoken_template: str = self.settings.template
        self.spoken_end_template: str = self.settings.end_template
        self.spoken_speed: float = self.settings.speed
        self.changes_file = settings_file.with_name("changes.json")
        self.changes_unreadable = False
        self.changes_backed_up = False
        self.changes: tuple[Change, ...] = self._load_changes(clock().date())

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
        self.instants: tuple[Instant, ...] = ()  # today's, oldest first
        self._outcomes: dict[OutcomeKey, Status] = {}
        self._playing: dict[int, _Group] = {}  # queued or sounding, by number
        self._sounding: _Group | _Test | None = None  # what ``cut`` would cut
        # Instant announcements and tests whose speech is being made.
        self._preparing: set[Instant | _Test] = set()
        self._wanted: queue.SimpleQueue[_Wanted] = queue.SimpleQueue()
        self._speaking = False  # the thread that makes that speech runs
        self._numbers = itertools.count(1)
        self._numbers_lock = threading.Lock()  # the scheduler's thread takes some

        self._cache = AudioCache(cache_directory, provider, self.settings.voice)
        self._scheduler = Scheduler(
            self.timetable, self._on_due, self._on_missed, clock, interval
        )
        self._player = Player(
            self._cache.find,
            output,
            self._on_played,
            on_started=lambda tag: self._events.put(_Started(tag)),
            on_speaking=lambda tag, index: self._events.put(_Speaking(tag, index)),
        )

    def start(self) -> None:
        _log.info(
            "Version %s started; the settings are in %s", VERSION, self.settings_file
        )
        # The timetable comes first, so the scheduler's first look knows what
        # is in the minute that it starts in.
        self.refresh()
        self._keep_a_spoken_speed()
        now = self._scheduler.start()
        self._player.start()
        # The scheduler leaves out the slot of the minute it starts in, which
        # a run that was closed a moment ago may have announced.
        slot = self.timetable.slot_at(now.date(), datetime.time(now.hour, now.minute))
        left = None if slot is None else untaken(now.date(), slot, ())
        if left is not None:
            for part in left.parts:
                self._outcomes[_key(now.date(), left, part)] = Status.PASSED
            _log.warning(
                "%s not announced, its minute had begun when the app started: %s",
                f"{left.time:%H:%M}",
                _quoted(left.texts),
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

    def set_templates(self, template: str, end_template: str) -> None:
        """Raises ``ValueError`` if one of the templates has no place for the name
        and ``OSError`` if they cannot be saved; nothing changes then.
        """
        self._save(
            dataclasses.replace(
                self.settings, template=template, end_template=end_template
            )
        )
        _log.info('Templates changed to "%s" and "%s"', template, end_template)
        self._rebuild()

    def set_end_announcements(self, on: bool) -> None:
        """Raises ``OSError`` if it cannot be saved; nothing changes then."""
        self._save(dataclasses.replace(self.settings, end_announcements=on))
        _log.info("The end of a session is %sannounced", "" if on else "not ")
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
        """Raises ``SlotTakenError`` if the announcement would share its minute
        with the start of a session or with another custom announcement, and
        ``OSError`` if it cannot be saved; nothing changes then.
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
        replaced = None if replacing is None else announcements[replacing]
        if replacing is None:
            announcements.append(announcement)
        else:
            announcements[replacing] = announcement
        self._save_announcements(announcements)
        if replaced is not None and (replaced.time, replaced.text) != (
            announcement.time,
            announcement.text,
        ):
            self._forget_changes(replaced)
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
        self._forget_changes(deleted)
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
        test = _Test(self._number())
        self.test_status = Status.PREPARING
        self._ask_for((test, text, self.settings.speed))

    def make_speech(self, texts: Iterable[str]) -> SpeechRequest:
        """``poll`` fills in the request."""
        speeds = tuple(dict.fromkeys((self.spoken_speed, self.settings.speed)))
        request = SpeechRequest(tuple(dict.fromkeys(texts)))
        self._ask_for((request, speeds))
        return request

    def rows(self) -> tuple[Row, ...]:
        playing = {key for group in self._playing.values() for key, _ in group.parts}
        return todays_rows(
            self.timetable, self._clock(), self._outcomes, self.instants, playing
        )

    @property
    def sounding(self) -> bool:
        """Whether ``cut`` has something to cut off."""
        return self._sounding is not None or bool(self._preparing)

    def replay(self, row: Row) -> bool:
        """The row then tells how the replay went."""
        if not row.replayable or row not in self.rows():
            return False
        if isinstance(row.key, Instant):
            _log.info('Instant announcement asked for again: "%s"', row.key.text)
            self._prepare(row.key)
            return True
        found = None if row.key is None else self._part(row.key)
        if found is None:
            return False
        slot, part = found
        today = self._clock().date()
        key = _key(today, slot, part)
        # What was there before the schedule changed may still be sounding.
        if any(
            queued == key
            for group in self._playing.values()
            for queued, _ in group.parts
        ):
            return False
        due = datetime.datetime.combine(today, slot.time)
        _log.info("%s asked for again: %s", f"{due:%H:%M}", _quoted([part.text]))
        self._queue(self._group(due, ((key, part.text),)), slot.speed)
        return True

    def announce(self, text: str) -> None:
        """Read ``text`` at once, after whatever is being read. Raises
        ``ValueError`` if there is nothing to read."""
        text = text.strip()
        if not text:
            raise ValueError("The announcement has no text")
        asked_at = self._clock()
        if self.instants and self.instants[-1].asked_at >= asked_at:
            # Each one its own moment, so that its outcome is its own.
            asked_at = self.instants[-1].asked_at + datetime.timedelta(microseconds=1)
        instant = Instant(asked_at, text)
        today = asked_at.date()
        self.instants = (
            *(item for item in self.instants if item.asked_at.date() == today),
            instant,
        )
        _log.info('Instant announcement requested: "%s"', text)
        self._prepare(instant)

    def cut(self) -> None:
        """What is queued after what is cut off is read as usual."""
        if not self.sounding:
            return
        _log.info("Cut off by the operator")
        for item in self._preparing:
            if isinstance(item, Instant):
                self._outcomes[item.key] = Status.STOPPED
            else:
                self.test_status = Status.STOPPED
        self._preparing.clear()
        if self._sounding is not None:
            self._player.cut(self._sounding)
            # A second press must not cut off what comes next.
            self._sounding = None

    def editing(self, row: Row) -> Editing | None:
        """Return None if the row cannot be changed any more."""
        key = row.key
        current = {other.key: other for other in self.rows()}.get(key)
        planned = self._planned(key) if isinstance(key, tuple) else None
        if current is None or not current.editable or planned is None:
            return None
        slot, part = planned
        assert isinstance(key, tuple)
        change = self._change_or_new(key)
        if part.kind is Kind.CUSTOM:
            planned_text = part.text
        elif part.kind is Kind.END:
            planned_text = render(self.settings.end_template, part.name)
        else:
            planned_text = render(self.settings.template, part.name)
        end = self._end_of(row)
        shown_end = (
            end is not None
            and self.settings.end_announcements
            and self._clock() < end[1] - CHANGE_MARGIN
        )
        return Editing(
            part.kind,
            slot.time,
            slot.time if change.time is None else change.time,
            planned_text,
            planned_text if change.text is None else said(change.text, part.name),
            part.name,
            end[1].time() if end is not None and shown_end else None,
        )

    def skip(self, row: Row, skipped: bool = True) -> None:
        """The end of a session follows its start, if its time has not come. Raises
        ``ChangeError`` if the row's time has come and ``OSError`` if it cannot be
        saved; nothing changes then.
        """
        key = self._skippable(row)
        keys = [key]
        end = self._end_of(row)
        if end is not None:
            end_key, due = end
            now = self._clock()
            if (
                now < due - CHANGE_MARGIN
                if skipped
                else now - due <= LATE - CHANGE_MARGIN
            ):
                keys.append(end_key)
        self._save_changes(
            dataclasses.replace(self._change_or_new(each), skipped=skipped)
            for each in keys
        )
        for moment, kind, name in keys:
            _log.info(
                "%s of %s%s %s for today",
                kind.value.capitalize(),
                f"{moment:%H:%M}",
                f" for {name}" if name else "",
                "skipped" if skipped else "no longer skipped",
            )
        self._build()

    def edit(self, row: Row, time: datetime.time, text: str) -> None:
        """A new text needs its speech first: see ``make_speech``. Raises
        ``ChangeError`` if the change cannot be made and ``OSError`` if it cannot be
        saved; nothing changes then.
        """
        editing = self.editing(row)
        key = row.key
        if editing is None or not isinstance(key, tuple):
            raise ChangeError(ChangeProblem.GONE)
        text = text.strip()
        if not text:
            raise ChangeError(ChangeProblem.NO_TEXT)
        now = self._clock()
        # Wherever it goes, back to the plan too, has to be still to come.
        if time != editing.time and datetime.datetime.combine(now.date(), time) <= now:
            raise ChangeError(ChangeProblem.PAST)
        # What is said, as typed in a placeholder may still stand for the name.
        text = self.text_of(editing, text)
        reworded = text != editing.planned_text
        if reworded and self._cache.find(text, self.spoken_speed) is None:
            raise ChangeError(ChangeProblem.NO_SPEECH)
        self._save_changes(
            [
                dataclasses.replace(
                    self._change_or_new(key),
                    time=None if time == editing.planned else time,
                    text=text if reworded else None,
                )
            ]
        )
        _log.info(
            '%s changed for today: at %s, "%s"',
            f"{editing.planned:%H:%M}",
            f"{time:%H:%M}",
            text,
        )
        self._rebuild()

    @staticmethod
    def text_of(editing: Editing, text: str) -> str:
        return said(text, editing.name)

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
                case _Due(group):
                    # Outcomes of earlier days are of no use to today's preview.
                    self._outcomes = {
                        key: status
                        for key, status in self._outcomes.items()
                        if group.due - key[0] < _DAY
                    }
                    for key, _ in group.parts:
                        self._outcomes[key] = Status.QUEUED
                    self._playing[group.number] = group
                case _Missed(date, slot):
                    for part in slot.parts:
                        self._outcomes[_key(date, slot, part)] = Status.ASLEEP
                case _Started(tag=_Group() as group):
                    self._sounding = group
                case _Speaking(tag=_Group() as group, index=index):
                    # The part before it was heard, as far as anyone can tell
                    # until the group's outcome comes.
                    for key, _ in group.parts[:index]:
                        if self._outcomes.get(key) is Status.PLAYING:
                            self._outcomes[key] = Status.PLAYED
                    self._outcomes[group.parts[index][0]] = Status.PLAYING
                case _Started(tag=_Test() as test):
                    self._sounding = test
                    self.test_status = Status.PLAYING
                case Playback(tag=_Group() as group):
                    self._played(group, event)
                case Playback(tag=tag):
                    if self._sounding == tag:
                        self._sounding = None
                    self.test_status = outcome_status(event)
                case _Generated(templates, speed, unspoken):
                    self.generating = False
                    self.unspoken = unspoken
                    self._generated_at = self._clock()
                    if not unspoken:
                        self._speak_with(templates, speed)
                    if self._regenerate:
                        self._generate()
                case _Spoken(wanted, spoken):
                    self._spoken(wanted, spoken)

    def _played(self, group: _Group, playback: Playback) -> None:
        if self._sounding == group:
            self._sounding = None
        self._playing.pop(group.number, None)
        for index, (key, _) in enumerate(group.parts):
            self._outcomes[key] = part_status(playback, index)
        self.last_announcement = LastAnnouncement(
            group.due,
            tuple((key[1], key[2]) for key, _ in group.parts),
            outcome_status(playback),
        )

    def _spoken(self, wanted: _Wanted, spoken: bool) -> None:
        match wanted:
            case (SpeechRequest() as request, _):
                request.done, request.spoken = True, spoken
            case (Instant() | _Test() as item, text, speed):
                if item not in self._preparing:
                    return  # cut off meanwhile
                self._preparing.discard(item)
                if isinstance(item, _Test):
                    self.test_status = Status.QUEUED if spoken else Status.NO_SPEECH
                    if spoken:
                        self._player.enqueue((text,), item, speed)
                elif spoken:
                    group = self._group(item.asked_at, ((item.key, text),))
                    self._queue(group, speed)
                else:
                    self._outcomes[item.key] = Status.NO_SPEECH

    def _number(self) -> int:
        """Safe to call from any thread."""
        with self._numbers_lock:
            return next(self._numbers)

    def _group(
        self,
        due: datetime.datetime,
        parts: Iterable[tuple[OutcomeKey, str]],
    ) -> _Group:
        """Safe to call from any thread."""
        return _Group(self._number(), due, tuple(parts))

    def _queue(self, group: _Group, speed: float) -> None:
        for key, _ in group.parts:
            self._outcomes[key] = Status.QUEUED
        self._playing[group.number] = group
        self._player.enqueue(tuple(text for _, text in group.parts), group, speed)

    def _prepare(self, instant: Instant) -> None:
        """Make the speech of an instant announcement; ``poll`` then queues it."""
        self._outcomes[instant.key] = Status.PREPARING
        self._ask_for((instant, instant.text, self.settings.speed))

    def _ask_for(self, wanted: _Wanted) -> None:
        """Have speech made on a thread, as that can take a few seconds. It is
        made in the order asked for, so what is read at once is read in that
        order."""
        if not isinstance(wanted[0], SpeechRequest):
            self._preparing.add(wanted[0])
        self._wanted.put(wanted)
        if not self._speaking:
            self._speaking = True
            threading.Thread(target=self._speak, name="speech now", daemon=True).start()

    def _speak(self) -> None:
        """Runs on a thread, for as long as the app does."""
        while True:
            wanted = self._wanted.get()
            if isinstance(wanted[0], SpeechRequest):
                request, speeds = wanted
                texts = request.texts
            else:
                _, text, speed = wanted
                texts, speeds = (text,), (speed,)
            spoken = False
            try:
                spoken = not any(self._cache.ensure_all(texts, at) for at in speeds)
            except Exception:
                _log.exception("Generating speech to be read at once failed")
            self._events.put(_Spoken(wanted, spoken))

    def _part(self, key: PartId) -> tuple[Slot, Part] | None:
        today = self._clock().date()
        for slot in self.timetable.slots_on(today):
            for part in slot.parts:
                if part_id(today, slot, part) == key:
                    return slot, part
        return None

    def _planned(self, key: PartId) -> tuple[Slot, Part] | None:
        """Return the part with the key as the week has it, without today's
        change, and its slot."""
        today = self._clock().date()
        for slot in self.timetable.slots:
            if slot.weekday != today.weekday():
                continue
            for part in slot.parts:
                if part_id(today, slot, part) == key:
                    return slot, part
        return None

    def _end_of(self, row: Row) -> tuple[PartId, datetime.datetime] | None:
        """Return the key of the end of the session that ``row`` starts, and when
        that end is announced today; None if ``row`` is no start, or its end is
        announced on another day. The end need not be in the timetable, as
        the end of a session may be switched off.
        """
        if row.kind is not Kind.START or not isinstance(row.key, tuple):
            return None
        key = (row.key[0] + SESSION, Kind.END, row.key[2])
        today = self._clock().date()
        change = self._change(key)
        if change is not None and change.time is not None:
            due = datetime.datetime.combine(today, change.time)
        else:
            due = key[0] + datetime.timedelta(minutes=self.settings.delay)
        return (key, due) if due.date() == today else None

    def _change(self, key: PartId) -> Change | None:
        today = self._clock().date()
        for change in self.changes:
            if (change.day, change.set_for, change.kind, change.name) == (today, *key):
                return change
        return None

    def _change_or_new(self, key: PartId) -> Change:
        return self._change(key) or Change(self._clock().date(), *key)

    def _skippable(self, row: Row) -> PartId:
        """Raises ``ChangeError`` if the row can no longer be skipped."""
        key = row.key
        current = {other.key: other for other in self.rows()}.get(key)
        if (
            not isinstance(key, tuple)
            or current is None
            or not current.skippable
            or self._planned(key) is None
        ):
            raise ChangeError(ChangeProblem.GONE)
        return key

    def _forget_changes(self, announcement: CustomAnnouncement) -> None:
        """Let go of what was changed today about a custom announcement that is
        replaced or deleted, so that it is not taken for one put in its place.
        """
        kept = [
            change
            for change in self.changes
            if not (
                change.kind is Kind.CUSTOM
                and change.set_for.time() == announcement.time
            )
        ]
        if len(kept) == len(self.changes):
            return
        try:
            save_changes(kept, self.changes_file)
        except OSError:
            # The announcement itself was saved; the change is left as it is.
            _log.exception("Cannot let go of the changes of a custom announcement")
            return
        self.changes = tuple(kept)

    def _save_changes(self, changes: Iterable[Change]) -> None:
        """Raises ``OSError`` if the changes cannot be saved; nothing changes then."""
        today = self._clock().date()
        changes = tuple(changes)
        replaced = {_change_key(change) for change in changes}
        kept = tuple(
            other
            for other in self.changes
            if other.day >= today and _change_key(other) not in replaced
        )
        kept += tuple(change for change in changes if change.skipped or change.edited)
        save_changes(kept, self.changes_file)
        self.changes = kept
        # What is in the file is in use now, not the plan as such.
        self.changes_unreadable = False

    def _load_changes(self, today: datetime.date) -> tuple[Change, ...]:
        """A file that cannot be read is copied first: the next change written
        replaces it."""
        try:
            changes = load_changes(self.changes_file, today)
        except ChangesFileError:
            _log.exception("Cannot read today's changes; announcing as planned")
            self.changes_unreadable = True
            try:
                shutil.copyfile(self.changes_file, backup_copy(self.changes_file))
                self.changes_backed_up = True
            except OSError:
                _log.exception("Cannot keep a copy of the changes file")
            return ()
        if changes:
            _log.info("%d change(s) of today kept from before", len(changes))
        return changes

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
        """There is no button for reading the schedule file again, and a file on a
        network drive may turn up without having changed."""
        if self.schedule_error is None or abs(self._clock() - self._read_at) < _RETRY:
            return False
        _log.info("Trying again to read the schedule file")
        self.refresh()
        return True

    def _retry_speech(self) -> bool:
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
        self.timetable = self._timetable(
            self.spoken_template, self.spoken_end_template, self.spoken_speed
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

    def _timetable(self, template: str, end_template: str, speed: float) -> Timetable:
        return build_timetable(
            self._entries,
            self.settings.custom_announcements,
            template,
            speed,
            self.settings.delay,
            end_template if self.settings.end_announcements else None,
            self.changes,
        )

    def _keep_a_spoken_speed(self) -> None:
        """A speed that was chosen shortly before the app was closed may have no
        speech yet. The announcements then start with a speed that has, the
        one nearest to the normal speed, as they would have gone on with the
        one before.
        """

        def spoken(speed: float) -> bool:
            return all(
                self._cache.find(text, speed) is not None
                for slot in self.timetable.all_slots()
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

    def _speak_with(self, templates: tuple[str, str], speed: float) -> None:
        """Announce with ``templates`` and at ``speed``, now that the speech for
        that is there.
        """
        wanted = (self.settings.template, self.settings.end_template)
        if (templates, speed) != (wanted, self.settings.speed):
            return  # the speech is for settings that were replaced meanwhile
        spoken = (self.spoken_template, self.spoken_end_template)
        if (templates, speed) != (spoken, self.spoken_speed):
            _log.info(
                'Athletes are announced with "%s" and "%s" and everything at '
                "speed %s from now on",
                *templates,
                speed,
            )
            (self.spoken_template, self.spoken_end_template) = templates
            self.spoken_speed = speed
            self._build()

    def _generate(self) -> None:
        if self.generating:
            self._regenerate = True  # once more when the running round ends
            return
        self._regenerate = False
        self._generated_at = self._clock()
        templates = (self.settings.template, self.settings.end_template)
        speed = self.settings.speed
        wanted = self._timetable(*templates, speed)
        texts = tuple(text for slot in wanted.all_slots() for text in slot.texts)
        todo = sum(
            self._cache.find(text, speed) is None for text in dict.fromkeys(texts)
        )
        if not todo:
            self.unspoken = ()
            self._speak_with(templates, speed)
            return
        _log.info("Generating speech for %d sentence(s)", todo)
        self.generating = True
        threading.Thread(
            target=self._generate_all,
            args=(templates, speed, texts),
            name="speech",
            daemon=True,
        ).start()

    def _generate_all(
        self, templates: tuple[str, str], speed: float, texts: tuple[str, ...]
    ) -> None:
        """Runs on a thread."""
        unspoken = texts
        try:
            unspoken = self._cache.ensure_all(texts, speed)
            _log.info("Speech generated; %d sentence(s) without speech", len(unspoken))
        except Exception:
            _log.exception("Generating speech failed")
        finally:
            self._events.put(_Generated(templates, speed, unspoken))

    def _on_due(self, date: datetime.date, slot: Slot) -> None:
        """Called on the scheduler's thread, with the parts of the slot that are
        to be announced."""
        due = datetime.datetime.combine(date, slot.time)
        _log.info("%s due: %s", f"{due:%H:%M}", _quoted(slot.texts))
        group = self._group(
            due, ((_key(date, slot, part), part.text) for part in slot.parts)
        )
        self._events.put(_Due(group))
        self._player.enqueue(slot.texts, group, slot.speed)

    def _on_missed(self, date: datetime.date, slot: Slot) -> None:
        """Called on the scheduler's thread."""
        due = datetime.datetime.combine(date, slot.time)
        _log.warning(
            "%s skipped, the computer was asleep: %s",
            f"{due:%Y-%m-%d %H:%M}",
            _quoted(slot.texts),
        )
        self._events.put(_Missed(date, slot))

    def _on_played(self, playback: Playback) -> None:
        """Called on the player's thread."""
        group = playback.tag
        name = f"{group.due:%H:%M}" if isinstance(group, _Group) else "Test"
        skipped = {*playback.missing, *playback.late, *playback.cut}
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
        if playback.cut:
            _log.info("%s cut off before: %s", name, _quoted(playback.cut))
        self._events.put(playback)


def _key(date: datetime.date, slot: Slot, part: Part) -> OutcomeKey:
    return set_for(date, slot, part), part.kind, who(part)


def _change_key(change: Change) -> tuple[datetime.date, datetime.datetime, Kind, str]:
    return change.day, change.set_for, change.kind, change.name


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
