"""Start the app: ``uv run kleomarcus-announcer`` or ``python -m announcer``."""

import logging

from announcer.app import App
from announcer.logs import configure_logging, log_path
from announcer.player import PygameOutput
from announcer.settings import settings_path
from announcer.single_instance import claim
from announcer.tts import EdgeTtsProvider, audio_cache_dir
from announcer.ui.main_window import MainWindow, tell_already_running

_log = logging.getLogger(__name__)


def main() -> None:
    """Run the app until its window is closed."""
    log_file = log_path()
    configure_logging(log_file)
    settings_file = settings_path()
    try:
        # Held until the app ends; two copies would announce everything twice.
        claimed = claim(settings_file.with_name("announcer.lock"))
    except OSError:
        _log.exception("Cannot tell whether another copy is running; starting")
    else:
        if claimed is None:
            _log.warning("Another copy is already running; this one ends")
            tell_already_running()
            return

    app = App(settings_file, audio_cache_dir(), EdgeTtsProvider(), PygameOutput())
    app.start()
    try:
        MainWindow(app, log_file).run()
    finally:
        app.stop()


if __name__ == "__main__":
    main()
