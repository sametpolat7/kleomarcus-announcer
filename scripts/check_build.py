"""Start the packaged app and check that it comes up. Run with
``uv run scripts/check_build.py`` and the path of the app's executable file.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

from announcer import VERSION
from announcer.logs import log_path

_WAIT = 15.0  # seconds the app gets to start


def main() -> None:
    parser = argparse.ArgumentParser(description="Check that the packaged app starts.")
    parser.add_argument("executable", type=Path)
    parser.add_argument(
        "--screenshot", type=Path, help="save a picture of the screen in this file"
    )
    arguments = parser.parse_args()

    problems = _check(arguments.executable, arguments.screenshot)
    for problem in problems:
        print(f"FAILED: {problem}")
    if problems:
        sys.exit(1)
    print(f"Version {VERSION} starts.")


def _check(executable: Path, screenshot: Path | None) -> list[str]:
    problems = []
    # The folder that holds everything of the app: on macOS the bundle's
    # ``Contents``, which is one above the executable file's folder.
    folder = executable.parent.parent if sys.platform == "darwin" else executable.parent
    if not any(folder.rglob("assets/chime.wav")):
        problems.append("The chime is not in the package.")

    log = log_path()
    old = log.stat().st_size if log.exists() else 0  # the app adds to its log
    app = subprocess.Popen([executable])
    try:
        time.sleep(_WAIT)
        if screenshot is not None:
            _take_screenshot(screenshot)
        if app.poll() is not None:
            problems.append(f"The app ended by itself with code {app.returncode}.")
    finally:
        app.kill()
        app.wait()

    written = log.read_bytes()[old:].decode("utf-8") if log.exists() else ""
    print(f"The app wrote to {log}:\n{written}")
    if f"Version {VERSION} started" not in written:
        problems.append(f"The log does not say that version {VERSION} started.")
    if " ERROR " in written or " CRITICAL " in written:
        problems.append("The log holds an error.")
    return problems


def _take_screenshot(path: Path) -> None:
    if sys.platform == "darwin":
        command = ["screencapture", "-x", str(path)]
    else:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms, System.Drawing; "
            "$area = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
            "$image = New-Object System.Drawing.Bitmap $area.Width, $area.Height; "
            "[System.Drawing.Graphics]::FromImage($image).CopyFromScreen("
            "$area.Location, [System.Drawing.Point]::Empty, $area.Size); "
            f"$image.Save('{path}')"
        )
        command = ["powershell", "-NoProfile", "-Command", script]
    # The picture is only for a person to look at, so a failure is no problem.
    subprocess.run(command, check=False)


if __name__ == "__main__":
    main()
