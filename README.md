<p align="center">
  <img src="docs/logo.svg" width="88" alt="Kleomarcus Announcer logo">
</p>

<h1 align="center">Kleomarcus Announcer</h1>

<p align="center"><strong>Calls every athlete by name, right on time.</strong></p>

<p align="center">
  <a href="https://github.com/sametpolat7/kleomarcus-announcer/actions/workflows/ci.yml"><img src="https://github.com/sametpolat7/kleomarcus-announcer/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fsametpolat7%2Fkleomarcus-announcer%2Fmain%2Fpyproject.toml" alt="Python"></a>
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey" alt="Platform: Windows and macOS">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/sametpolat7/kleomarcus-announcer" alt="License"></a>
</p>

A desktop app that makes the lobby announcements of
[Kleomarcus Spor Akademi](https://www.kleomarcus.com). It reads the weekly
training schedule from an Excel file and, when a session starts, plays a chime
and calls each athlete by name through the computer's speakers.

<p align="center">
  <img src="docs/screenshot.png" width="760" alt="The main window, showing today's announcements">
</p>

## Features

- **Schedule from Excel.** One row per session. Save the file and the app
  picks up the change by itself.
- **A natural Turkish voice.** Each sentence is generated once, stored on the
  computer and played from there, so a lost connection does not silence a
  known schedule.
- **Custom announcements.** Free text at a fixed time, every day or on chosen
  weekdays, such as a closing reminder.
- **Today at a glance.** Every announcement of the day with its status:
  waiting, next, playing, played or skipped, and why.
- **On time or not at all.** Nothing is played late after a restart or a
  sleep; what was missed is shown instead.
- **Made for a front desk.** One window with a Turkish interface, a test
  button, a warning for every problem and a log file.

## Getting started

Packaged builds for Windows and macOS will be published with the first
release. Until then the app runs from source with
[uv](https://docs.astral.sh/uv/), which installs Python and every dependency
on first use:

```bash
git clone https://github.com/sametpolat7/kleomarcus-announcer.git
cd kleomarcus-announcer
uv run kleomarcus-announcer
```

1. Press **Dosya Seç…** and choose the schedule. The speech is generated in
   the background, which needs an internet connection the first time.
2. Press **Test** to hear the announcement with a made-up name.
3. Leave the app running. It announces each session at its time.

## Schedule file

The schedule is an Excel workbook; only `.xlsx` files are accepted. The first
visible worksheet needs a header row with these columns, in any order:

| Column     | Content                                    | Example       |
| ---------- | ------------------------------------------ | ------------- |
| `Ad Soyad` | The athlete's full name                    | `Ayşe Yılmaz` |
| `Gün`      | Turkish weekday name                       | `Salı`        |
| `Saat`     | Start time, as a time cell or `HH:MM` text | `18:30`       |

Each row is one session, so an athlete who trains three times a week has three
rows. Athletes who start together are announced together, after one chime.
Other columns are ignored, and headers and weekday names are matched without
regard to case or Turkish accents. A row with a missing name, an unknown day
or an invalid time is skipped and reported in the window; the rest of the
schedule still loads.

Write times with a colon. Text such as `18.30` is accepted too, but Excel may
turn a dotted value into a number or a date, and such a row is skipped.

[`sample_schedule.xlsx`](sample_schedule.xlsx) is a working example with
fictional names.

## How it behaves

- **Announcement text.** Athletes are announced with a template, by default
  `{ad_soyad}, antrenmanınız başlamıştır. İyi antrenmanlar dileriz.`, where
  `{ad_soyad}` stands for the name and may be moved anywhere in the sentence.
  A new template is used once all of its speech exists.
- **Timing.** A session is announced within the first 30 seconds of its
  minute, by an app that was already running when the minute began. The app
  does not keep the computer awake: an announcement that falls into a sleep
  is skipped and shown as such.
- **Conflicts.** A custom announcement needs a minute of its own. Where
  athletes start in the same minute, the athletes are announced and the
  window says so.
- **Internet.** Only generating new speech needs a connection. Speech that
  could not be generated is tried again every five minutes.
- **One copy.** A second copy of the app says that one is running and exits.

## Files on the computer

| What             | Windows                                                  | macOS                                                            |
| ---------------- | -------------------------------------------------------- | ---------------------------------------------------------------- |
| Settings         | `%LOCALAPPDATA%\Kleomarcus Announcer\config.json`        | `~/Library/Application Support/Kleomarcus Announcer/config.json` |
| Log              | `%LOCALAPPDATA%\Kleomarcus Announcer\Logs\announcer.log` | `~/Library/Logs/Kleomarcus Announcer/announcer.log`              |
| Generated speech | `%LOCALAPPDATA%\Kleomarcus Announcer\Cache\audio`        | `~/Library/Caches/Kleomarcus Announcer/audio`                    |

The settings hold the chosen schedule file, the template, the custom
announcements and the voice (`tr-TR-EmelNeural`; any voice of the service can
be set in `config.json`). If the file is damaged the app starts with the
defaults and keeps a copy as `config.json.backup`. The **Kayıtlar** button
opens the log. Deleting the speech folder is safe: the speech is generated
again, which also replaces a sentence that sounds cut off.

## Privacy

The schedule file never leaves the computer. To be spoken, each sentence,
including the athlete's name, is sent to Microsoft Edge's online read-aloud
service whenever its speech is generated, through
[edge-tts](https://github.com/rany2/edge-tts), an unofficial client for it.
The log and the stored speech contain athlete names and stay on the computer.

> [!IMPORTANT]
> This is a public repository. Never commit a real schedule: `.gitignore`
> excludes every `.xlsx` file except the sample.

## Development

```bash
uv sync               # create the environment
uv run pytest         # tests
uv run ruff check     # lint
uv run ruff format    # format
uv run mypy           # type check
```

GitHub Actions runs the same checks on Windows and macOS for every pull
request and every push to `main`.

The code lives in `src/announcer`, in small modules with one job each:

| Module                      | Job                                                         |
| --------------------------- | ----------------------------------------------------------- |
| `loader`                    | Read and validate the Excel schedule                        |
| `timetable`                 | Combine sessions and custom announcements into weekly slots |
| `scheduler`                 | Decide when a slot is due                                   |
| `tts`                       | Generate speech and keep it on disk                         |
| `player`, `chime`           | Play the chime and the speech                               |
| `logo`                      | Draw the logo and the window icons                          |
| `settings`, `files`, `logs` | Settings, safe file writes and the log                      |
| `app`, `today`              | Tie everything together behind the window                   |
| `ui`                        | The tkinter window and its Turkish wording                  |

The chime (`scripts/make_chime.py`) and the logo (`scripts/make_logo.py`) are
generated by code, so the repository holds no third-party audio or artwork.

## License

[MIT](LICENSE)
