# Kleomarcus Announcer

[![CI](https://github.com/sametpolat7/kleomarcus-announcer/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/sametpolat7/kleomarcus-announcer/actions/workflows/ci.yml)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fsametpolat7%2Fkleomarcus-announcer%2Fmain%2Fpyproject.toml)](pyproject.toml)
[![License](https://img.shields.io/github/license/sametpolat7/kleomarcus-announcer)](LICENSE)

A small desktop app for [Kleomarcus Spor Akademi](https://www.kleomarcus.com)
that plays scheduled voice announcements in the lobby: it reads the training
schedule from an Excel file and announces each athlete by name when their
session starts.

The project is under active development. Setup and usage instructions will be
added as the app takes shape.

## Schedule file

The schedule is an `.xlsx` workbook. The first worksheet needs a header row
with these columns, in any order:

| Column     | Content                                    | Example       |
| ---------- | ------------------------------------------ | ------------- |
| `Ad Soyad` | The athlete's full name                    | `Ayşe Yılmaz` |
| `Gün`      | Turkish weekday name                       | `Salı`        |
| `Saat`     | Start time, as a time cell or `HH:MM` text | `18:30`       |

Each row is one session, so an athlete who trains three times a week has three
rows. Other columns are ignored, and the first visible worksheet is used.
Headers and weekday names are matched without regard to case or Turkish
accents. Rows with a missing name, an unknown day or an invalid time are
skipped and reported; the rest of the schedule still loads.

Write times with a colon. Text such as `18.30` is accepted too, but Excel may
turn a dotted value into a number or a date, and such a row is then skipped.

[`sample_schedule.xlsx`](sample_schedule.xlsx) is a working example with
fictional names.

> [!IMPORTANT]
> This is a public repository. Never commit a real schedule: `.gitignore`
> excludes every `.xlsx` file except the sample.

## Development

Requires [uv](https://docs.astral.sh/uv/). It installs the pinned Python 3.14
and all dependencies on first use.

```bash
uv sync                      # create the environment
uv run kleomarcus-announcer  # start the app
uv run pytest                # tests
uv run ruff check            # lint
uv run ruff format           # format
uv run mypy                  # type check
```

GitHub Actions runs the same checks on Windows and macOS for every pull request
and every push to `main`.

## License

[MIT](LICENSE)
