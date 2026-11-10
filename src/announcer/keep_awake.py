"""Keep the computer from going to sleep while the app runs.

A sleeping computer announces nothing. This is the only part of the app that
differs between Windows and macOS. The display may still turn off.
"""

import logging
import os
import subprocess
import sys

_log = logging.getLogger(__name__)

# Flags of the Windows ``SetThreadExecutionState`` call.
_ES_CONTINUOUS = 0x80000000  # the request lasts until it is changed
_ES_SYSTEM_REQUIRED = 0x00000001  # the system must not go to sleep by itself

_CAFFEINATE = "/usr/bin/caffeinate"  # ships with macOS

if sys.platform == "win32":
    import ctypes

    def _set_execution_state(flags: int) -> None:
        ctypes.windll.kernel32.SetThreadExecutionState(flags)


class KeepAwake:
    """Hold the computer awake between ``start`` and ``stop``.

    On Windows the request belongs to the calling thread, so call both from
    the thread that lives as long as the app: the main thread.
    """

    def __init__(self) -> None:
        self._caffeinate: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        """Ask the system not to sleep while it is idle."""
        if sys.platform == "win32":
            _set_execution_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
        elif sys.platform == "darwin" and self._caffeinate is None:
            try:
                # "-i" prevents idle sleep; "-w" ends caffeinate together with
                # this process, so it cannot be left behind.
                self._caffeinate = subprocess.Popen(
                    [_CAFFEINATE, "-i", "-w", str(os.getpid())]
                )
            except OSError:
                _log.exception("Cannot keep the computer awake")

    def stop(self) -> None:
        """Let the system sleep again."""
        if sys.platform == "win32":
            _set_execution_state(_ES_CONTINUOUS)
        elif self._caffeinate is not None:
            self._caffeinate.terminate()
            self._caffeinate.wait()
            self._caffeinate = None
