import errno
import sys
from pathlib import Path
from typing import IO

if sys.platform == "win32":
    import ctypes
    import msvcrt
else:
    import fcntl

# The installer cannot see the lock below, so on Windows the app also holds a
# mutex of this name: ``AppMutex`` in ``packaging/installer.iss``.
MUTEX_NAME = "KleomarcusAnnouncerRunning"


def claim(path: Path) -> IO[bytes] | None:
    """Mark the app as running; return None if another copy has done so.

    The mark is a lock on the file at ``path``, which the system lets go of
    when the app ends, however it ends. Keep the returned file open until
    then. Raises ``OSError`` if the file cannot be created or locked at all,
    which says nothing about another copy.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    file = path.open("a+b")
    try:
        if sys.platform == "win32":
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            # Never closed, so it is held until the app ends, like the lock.
            ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        else:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        file.close()
        # These are how the two systems say that someone else holds the lock.
        if error.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
            return None
        raise
    return file
