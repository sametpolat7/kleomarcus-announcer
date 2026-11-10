"""Keep a log file of what the app did, in lines that are easy to read back.

Every module logs through ``logging.getLogger(__name__)``. This module only
decides where the lines go and what they look like, for example::

    2026-11-10 18:00:00 INFO    announcer.app: 18:00 announced: "Ayşe Yılmaz, ..."
    2026-11-10 18:30:00 WARNING announcer.app: 18:30 skipped, no stored speech: ...

The file holds athlete names, so it stays on the computer, next to the app's
other per-user files.
"""

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

from platformdirs import user_log_dir

from announcer import APP_NAME

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_MAX_BYTES = 1_000_000  # a file that grows past this is set aside as ".1"
_OLD_FILES = 3  # how many files that were set aside are kept

_log = logging.getLogger(__name__)


def log_path() -> Path:
    """Return where the log file lives for the current user."""
    return Path(user_log_dir(APP_NAME, appauthor=False)) / "announcer.log"


def configure_logging(path: Path) -> None:
    """Send the app's log lines, and every error that nothing caught, to ``path``.

    The app's own modules log from ``INFO`` up and the libraries from
    ``WARNING`` up. If the file cannot be opened the app runs without a log.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=_MAX_BYTES, backupCount=_OLD_FILES, encoding="utf-8"
        )
    except OSError:
        return
    handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
    logging.getLogger().addHandler(handler)
    logging.getLogger("announcer").setLevel(logging.INFO)

    # A windowed app has no console, so an error that reaches the top of a
    # thread would otherwise vanish.
    sys.excepthook = _log_uncaught
    threading.excepthook = lambda args: _log_uncaught(
        args.exc_type, args.exc_value, args.exc_traceback
    )


def _log_uncaught(
    kind: type[BaseException],
    error: BaseException | None,
    trace: TracebackType | None,
) -> None:
    """Log an error that nothing caught, with its traceback."""
    _log.critical("Uncaught error", exc_info=(kind, error or kind(), trace))
