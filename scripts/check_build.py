"""Check that the packaged app works: it starts, reads the sample schedule,
generates the speech and makes an announcement, on a sound device that plays
nothing. Run with ``uv run scripts/check_build.py`` and the path of the app's
executable file, as a user who has no settings of the app yet. It needs a
network connection and takes up to three minutes.
"""

import argparse
import datetime
import os
import subprocess
import sys
import time
from pathlib import Path

from announcer import VERSION
from announcer.logs import log_path
from announcer.models import CustomAnnouncement
from announcer.settings import Settings, save_settings, settings_path
from announcer.single_instance import MUTEX_NAME

SCHEDULE = Path(__file__).resolve().parent.parent / "sample_schedule.xlsx"

_TEXT = "Deneme anonsu."
_LATEST = 60.0  # seconds after its minute by which the announcement must be over
_SETTLE = 3.0  # seconds for the window to show what has happened


def main() -> None:
    parser = argparse.ArgumentParser(description="Check that the packaged app works.")
    parser.add_argument("executable", type=Path)
    parser.add_argument(
        "--screenshot", type=Path, help="save a picture of the screen in this file"
    )
    arguments = parser.parse_args()

    settings = settings_path()
    if settings.exists():
        sys.exit(f"{settings} exists; this check is for a user without settings.")
    # The next minute but one, so the app is running when that minute begins.
    due = (datetime.datetime.now() + datetime.timedelta(minutes=2)).replace(
        second=0, microsecond=0
    )
    save_settings(
        Settings(
            schedule_path=SCHEDULE,
            custom_announcements=(CustomAnnouncement(_TEXT, due.time()),),
        ),
        settings,
    )
    try:
        problems = _check(arguments.executable, due, arguments.screenshot)
    finally:
        settings.unlink(missing_ok=True)

    for problem in problems:
        print(f"FAILED: {problem}")
    if problems:
        sys.exit(1)
    print(f"Version {VERSION} works.")


def _check(
    executable: Path, due: datetime.datetime, screenshot: Path | None
) -> list[str]:
    problems = []
    # The folder that holds everything of the app: on macOS the bundle's
    # ``Contents``, which is one above the executable file's folder.
    folder = executable.parent.parent if sys.platform == "darwin" else executable.parent
    if not any(folder.rglob("assets/chime.wav")):
        problems.append("The chime is not in the package.")

    log = log_path()
    old = log.stat().st_size if log.exists() else 0  # the app adds to its log

    def written() -> str:
        return log.read_bytes()[old:].decode("utf-8") if log.exists() else ""

    announced = f'announced: "{_TEXT}"'
    latest = due + datetime.timedelta(seconds=_LATEST)
    # SDL's "dummy" device takes the sound and plays nothing.
    app = subprocess.Popen([executable], env={**os.environ, "SDL_AUDIODRIVER": "dummy"})
    try:
        while (
            announced not in written()
            and app.poll() is None
            and datetime.datetime.now() < latest
        ):
            time.sleep(1)
        time.sleep(_SETTLE)
        if screenshot is not None:
            _take_screenshot(screenshot)
        if app.poll() is not None:
            problems.append(f"The app ended by itself with code {app.returncode}.")
        elif not _installer_sees_the_app():
            problems.append("The app does not hold the mutex the installer looks for.")
    finally:
        app.kill()
        app.wait()

    text = written()
    print(f"The app wrote to {log}:\n{text}")
    for expected in (
        f"Version {VERSION} started",
        "Schedule loaded from",
        "Speech generated; 0 sentence(s) without speech",
        announced,
    ):
        if expected not in text:
            problems.append(f'The log does not say "{expected}".')
    if " ERROR " in text or " CRITICAL " in text:
        problems.append("The log holds an error.")
    return problems


def _installer_sees_the_app() -> bool:
    if sys.platform != "win32":
        return True  # only the Windows installer looks
    import ctypes

    synchronize = 0x00100000  # the right to wait for the mutex; any right would do
    return bool(ctypes.windll.kernel32.OpenMutexW(synchronize, False, MUTEX_NAME))


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
