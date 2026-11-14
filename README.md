<p align="center">
  <img src="docs/logo.svg" width="88" alt="Kleomarcus Announcer logo">
</p>

<h1 align="center">Kleomarcus Announcer</h1>

<p align="center">Scheduled voice announcements for a sports club lobby.</p>

<p align="center">
  <a href="https://github.com/sametpolat7/kleomarcus-announcer/actions/workflows/ci.yml"><img src="https://github.com/sametpolat7/kleomarcus-announcer/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://github.com/sametpolat7/kleomarcus-announcer/releases/latest"><img src="https://img.shields.io/github/v/release/sametpolat7/kleomarcus-announcer" alt="Latest release"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fsametpolat7%2Fkleomarcus-announcer%2Fmain%2Fpyproject.toml" alt="Python"></a>
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey" alt="Platform: Windows and macOS">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/sametpolat7/kleomarcus-announcer" alt="License"></a>
</p>

A desktop app that makes the lobby announcements of
[Kleomarcus Spor Akademi](https://www.kleomarcus.com). It reads the weekly
training schedule from an Excel file and, when a session starts and again when
it ends an hour later, plays a chime and calls each athlete by name through the
computer's speakers.

<p align="center">
  <img src="docs/screenshot.png" width="860" alt="The main window, showing today's announcements">
</p>

## Features

- **Schedule from Excel.** One row per session. A saved file is read again
  automatically.
- **Turkish text-to-speech.** Each sentence is generated once, stored on the
  computer and played from there, so stored announcements play without a
  connection.
- **Start and end of a session.** Each athlete is welcomed when the session
  starts and seen off when it ends, an hour later, each with a template of
  its own. The end can be switched off.
- **Custom announcements.** Free text at a fixed time, every day or on chosen
  weekdays, such as a closing reminder.
- **Today's announcements.** A table of the day's announcements, one row per
  athlete, with the sentence that is read, its kind and its status: waiting,
  next, queued, playing, played or skipped, and why.
- **Changes for today.** An athlete's announcement, or a custom one, that is
  still to come can be skipped, moved or reworded, for today only.
- **Read again, read now, stop.** An announcement whose time has come can be
  read once more, a sentence typed in can be read at once, and whatever is
  being read can be cut off.
- **Speed and delay.** The speech can be made slower or faster, and every
  announcement can be made a few minutes after its time, for athletes who
  arrive late.
- **No late announcements.** An announcement missed during a restart or a
  sleep is not played afterwards; it is shown as skipped.
- **Turkish interface.** One window that fills the screen, with test
  buttons, warnings for problems and a log file.

## Install

Download the file for your system from the
[latest release](https://github.com/sametpolat7/kleomarcus-announcer/releases/latest):

| System                           | File                                                   | What to do                                                                                                                                |
| -------------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Windows 10 or 11, 64-bit         | `Kleomarcus-Announcer-<version>-windows-x64-setup.exe` | Run it. It installs for the current user, without administrator rights, and adds shortcuts to the Start menu and, if wanted, the desktop. |
| macOS 11 or later, Apple silicon | `Kleomarcus-Announcer-<version>-macos-arm64.dmg`       | Open it and drag the app into the Applications folder.                                                                                    |

The downloads are not code-signed, so both systems ask before the first
start:

- **Windows** shows "Windows protected your PC". Choose **More info**, then
  **Run anyway**.
- **macOS** says that Apple could not verify that the app is free of malware.
  Choose **Done**, then open **System Settings**, go to **Privacy & Security**,
  scroll down to **Security** and choose **Open Anyway**. The button is there
  for about an hour after the attempt to open the app. Confirm in the dialog
  that follows and enter the login password. On macOS 12 and earlier,
  Control-click the app and choose **Open** instead.

Whether a download was built by this repository can be checked with the
[GitHub CLI](https://cli.github.com):
`gh attestation verify <file> --repo sametpolat7/kleomarcus-announcer`.

To update, close the app and install the new version the same way. The
settings and the stored speech are kept. On an Intel Mac the app runs
[from source](#development).

## First steps

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

- **Window.** The window opens maximized. **F11** (on macOS also
  **Control-Command-F**) switches to full screen, which hides the taskbar or
  the menu bar and the Dock, and back; **Esc** leaves full screen too.
- **Announcement text.** Athletes are announced with two templates: at the
  start of the session, by default
  `{ad_soyad}, antrenmanınız başlamıştır. İyi antrenmanlar dileriz.`, and at
  its end, by default
  `{ad_soyad}, antrenmanınız sona ermiştir. Görüşmek üzere.`. `{ad_soyad}`
  stands for the name and may be moved anywhere in the sentence. New
  templates are used once all of their speech exists.
- **End of a session.** A session lasts an hour, so its end is announced an
  hour after its start; **Bitiş anonsu** switches that off and on. When one
  session ends in the minute that another starts, both are announced after a
  single chime, the end first. An athlete who has two sessions in a row is
  seen off and welcomed again.
- **Speed.** **Ses hızı** sets how fast everything is spoken, from 0.8 to 1.2
  times the speed of the voice. The speech is generated again for a new
  speed, which needs a connection, and the new speed is used once all of its
  speech exists. **Test** speaks at the chosen speed at once.
- **Delay.** **Gecikme süresi** moves every announcement, of athletes and
  custom ones alike, by 1 to 10 minutes: with a delay of one minute, a
  session listed for 19:00 is announced at 19:01 and its end at 20:01.
  Today's table shows the time at which an announcement is made. A change of
  the delay repeats nothing that was announced, and what a shorter delay puts
  behind the clock is announced at once.
- **Today's table.** Each row is one announcement of the day and shows the
  sentence that is read: the start or the end of one athlete's session, a
  custom announcement or one read with **Anlık Anons**. Athletes due in the
  same minute are read one after the other after a single chime; the row
  being read says so, those after it are queued. The buttons beside the table
  act on the selected row; nothing is read by a double click or a key.
- **Skip.** **Atla** leaves out a row whose time has not come, today only; it
  is shown struck through. Skipping an athlete's start skips the end of that
  session too. **Atlamayı Geri Al** brings a row back, also within the first
  half minute of its time; for a start it brings back the end as well, and
  the end can be brought back by itself.
- **Edit.** **Düzenle…**, or a double click, changes a row whose time has
  not come, today only: the time it is read at and the sentence that is
  read. Moving a start leaves the end of that session where it is. The delay
  is not added to a time chosen there. A new sentence is saved once its
  speech has been generated, which needs a connection. **Plana Dön** fills in
  the plan again. The schedule file and the custom announcements stay as they
  are, and tomorrow is as planned. Skipped and edited rows are kept when the
  app is restarted that day. A row can no longer be skipped or edited in the
  last two seconds before its time.
- **Read again.** **Tekrar Oku** reads the selected row of today's table once
  more, after the chime: that athlete's sentence, or the custom or typed-in
  announcement. It is there for a row whose time has come, whether it was
  announced or skipped, once the rest of its minute has been read, and the row
  then tells how that went.
- **Read now.** **Anlık Anons…** reads a sentence that is typed in at once,
  after whatever is being read, and adds it to today's table. Its speech is
  generated first, which needs a connection.
- **Stop.** **Durdur** cuts off what is being read, and drops an announcement
  typed in or a test whose speech is still being generated. What is queued
  after it is still read.
- **Timing.** A session is announced within the first 30 seconds of its
  minute, by an app that was already running when the minute began. The app
  does not keep the computer awake: an announcement that falls into a sleep
  is skipped and shown as such.
- **Conflicts.** A custom announcement may not share its minute with the
  start of a session or with another custom announcement. Where athletes
  start in the same minute, the athletes are announced and the window says
  so. Where sessions only end, the custom announcement is read after their
  end, after the same chime. A row moved with **Düzenle…** joins whatever is
  in its new minute.
- **Internet.** Only generating new speech needs a connection. Speech that
  could not be generated is tried again every five minutes.
- **One copy.** A second copy of the app says that one is running and exits.

## Files on the computer

| What             | Windows                                                  | macOS                                                             |
| ---------------- | -------------------------------------------------------- | ----------------------------------------------------------------- |
| Settings         | `%LOCALAPPDATA%\Kleomarcus Announcer\config.json`        | `~/Library/Application Support/Kleomarcus Announcer/config.json`  |
| Today's changes  | `%LOCALAPPDATA%\Kleomarcus Announcer\changes.json`       | `~/Library/Application Support/Kleomarcus Announcer/changes.json` |
| Log              | `%LOCALAPPDATA%\Kleomarcus Announcer\Logs\announcer.log` | `~/Library/Logs/Kleomarcus Announcer/announcer.log`               |
| Generated speech | `%LOCALAPPDATA%\Kleomarcus Announcer\Cache\audio`        | `~/Library/Caches/Kleomarcus Announcer/audio`                     |

The settings hold the chosen schedule file, the templates, whether the end of
a session is announced, the custom announcements, the speed, the delay and
the voice (`tr-TR-EmelNeural`; any
voice of the service can be set in `config.json`). If the file is damaged the app starts with the
defaults and keeps a copy as `config.json.backup`. Today's changes are kept
in `changes.json` until the day is over; if that file is damaged, the day is
announced as planned and a copy is kept as `changes.json.backup`. The
**Kayıtlar** button
opens the log. Deleting the speech folder is safe: the speech is generated
again, which also replaces a sentence that sounds cut off. Removing the app
leaves these files in place; delete the folders by hand to remove them too.

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

The app runs from source with [uv](https://docs.astral.sh/uv/), which
installs Python and every dependency on first use:

```bash
git clone https://github.com/sametpolat7/kleomarcus-announcer.git
cd kleomarcus-announcer
uv run kleomarcus-announcer
```

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
| `changes`                   | Today's skipped and edited announcements                    |
| `app`, `today`              | Tie everything together behind the window                   |
| `ui`                        | The tkinter window and its Turkish wording                  |

The chime (`scripts/make_chime.py`) and the logo (`scripts/make_logo.py`) are
generated by code, so the repository holds no third-party audio or artwork.

### Releases

`uv run pyinstaller packaging/announcer.spec` packages the app into `dist`
with [PyInstaller](https://pyinstaller.org): an `.app` on macOS and, on
Windows, a folder that [Inno Setup](https://jrsoftware.org/isinfo.php) turns
into the installer (`packaging/installer.iss`).

The [release workflow](.github/workflows/release.yml) does this on both
systems for every pull request, installs the result and checks that the
packaged app generates speech and makes an announcement
(`scripts/check_build.py`). To publish a version, set it in `pyproject.toml`,
merge, and push a tag with the same number, such as `v1.0.0`, on `main`: the
workflow then attaches the downloads to a new release.

## License

[MIT](LICENSE)
