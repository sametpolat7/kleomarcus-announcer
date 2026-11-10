"""Tests for the log file."""

import logging
import re
import sys
import threading
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from announcer import APP_NAME
from announcer.logs import configure_logging, log_path


@pytest.fixture
def path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A log file that logging is taken off again after the test."""
    # Restored by ``monkeypatch`` after ``configure_logging`` replaces them.
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    yield tmp_path / "logs" / "announcer.log"
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, RotatingFileHandler):
            root.removeHandler(handler)
            handler.close()


def test_log_path_is_a_file_of_the_app() -> None:
    location = log_path()

    assert location.name == "announcer.log"
    assert APP_NAME in location.parts
    assert location.is_absolute()


def test_line_holds_the_time_level_module_and_message(path: Path) -> None:
    configure_logging(path)

    logging.getLogger("announcer.app").info("18:00 announced: Ayşe Yılmaz")

    line = path.read_text(encoding="utf-8")
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d INFO    announcer\.app: "
        r"18:00 announced: Ayşe Yılmaz\n",
        line,
    )


def test_libraries_are_logged_from_warnings_up(path: Path) -> None:
    configure_logging(path)

    logging.getLogger("aiohttp.client").info("chatter")
    logging.getLogger("aiohttp.client").warning("trouble")

    text = path.read_text(encoding="utf-8")
    assert "chatter" not in text
    assert "WARNING aiohttp.client: trouble" in text


def test_error_that_nothing_caught_is_logged_with_its_traceback(path: Path) -> None:
    configure_logging(path)

    try:
        raise RuntimeError("something broke")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())

    text = path.read_text(encoding="utf-8")
    assert "CRITICAL announcer.logs: Uncaught error" in text
    assert "RuntimeError: something broke" in text


def test_error_that_ends_a_thread_is_logged(path: Path) -> None:
    configure_logging(path)

    def fail() -> None:
        raise RuntimeError("the thread broke")

    thread = threading.Thread(target=fail)
    thread.start()
    thread.join()

    assert "RuntimeError: the thread broke" in path.read_text(encoding="utf-8")


def test_full_file_is_set_aside_and_a_new_one_started(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("announcer.logs._MAX_BYTES", 200)
    configure_logging(path)

    for number in range(20):
        logging.getLogger("announcer").info("line %d", number)

    assert path.with_name("announcer.log.1").is_file()
    assert len(list(path.parent.iterdir())) == 4  # the file and three old ones
    assert "line 19" in path.read_text(encoding="utf-8")


def test_app_runs_without_a_log_when_the_file_cannot_be_opened(
    tmp_path: Path, path: Path
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the log folder should be")
    handlers = list(logging.getLogger().handlers)

    configure_logging(blocker / "announcer.log")

    assert logging.getLogger().handlers == handlers
