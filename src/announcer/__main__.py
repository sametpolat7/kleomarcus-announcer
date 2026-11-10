"""Start the app: ``uv run kleomarcus-announcer`` or ``python -m announcer``."""

from announcer.app import App
from announcer.logs import configure_logging, log_path
from announcer.player import PygameOutput
from announcer.settings import settings_path
from announcer.tts import EdgeTtsProvider, audio_cache_dir
from announcer.ui.main_window import MainWindow


def main() -> None:
    """Run the app until its window is closed."""
    log_file = log_path()
    configure_logging(log_file)
    app = App(settings_path(), audio_cache_dir(), EdgeTtsProvider(), PygameOutput())
    app.start()
    try:
        MainWindow(app, log_file).run()
    finally:
        app.stop()


if __name__ == "__main__":
    main()
