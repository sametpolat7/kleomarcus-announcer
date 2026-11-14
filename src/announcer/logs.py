"""Every module logs through ``logging.getLogger(__name__)``. The log file holds
athlete names, so it stays on the computer, next to the app's other per-user
files.
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
_MAX_BYTES = 1_000_000
_OLD_FILES = 3

_log = logging.getLogger(__name__)


def log_path() -> Path:
    return Path(user_log_dir(APP_NAME, appauthor=False)) / "announcer.log"


def configure_logging(path: Path) -> None:
    """If the file cannot be opened the app runs without a log."""
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
    _log.critical("Uncaught error", exc_info=(kind, error or kind(), trace))
