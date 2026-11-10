"""Let only one copy of the app run, so that nothing is announced twice."""

import sys
from pathlib import Path
from typing import IO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


def claim(path: Path) -> IO[bytes] | None:
    """Mark the app as running; return None if another copy has done so.

    The mark is a lock on the file at ``path``, which the system lets go of
    when the app ends, however it ends. Keep the returned file open until
    then. Raises ``OSError`` if the file cannot be created.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    file = path.open("a+b")
    try:
        if sys.platform == "win32":
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        file.close()
        return None
    return file
