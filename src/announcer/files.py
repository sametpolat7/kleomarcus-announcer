"""Write files so that a reader never sees one that is half-written."""

import os
import tempfile
from pathlib import Path


def write_atomically(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path``, creating its folder if needed.

    The data goes to a temporary file that is flushed to disk and then
    replaces ``path``. A write that is cut short, even by a power loss,
    therefore leaves the previous file, or no file, in place. Raises
    ``OSError`` if the file cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique name, so two writers of the same file cannot disturb each other.
    handle, name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
    )
    temporary = Path(name)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
