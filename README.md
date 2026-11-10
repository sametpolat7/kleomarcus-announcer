# Kleomarcus Announcer

A small desktop app for [Kleomarcus Spor Akademi](https://www.kleomarcus.com)
that plays scheduled voice announcements in the lobby: it reads the training
schedule from an Excel file and announces each athlete by name when their
session starts.

The project is under active development. Setup and usage instructions will be
added as the app takes shape.

## Schedule file

The schedule is an `.xlsx` workbook. The first worksheet needs a header row
with these columns, in any order:

| Column     | Content                                   | Example       |
| ---------- | ----------------------------------------- | ------------- |
| `Ad Soyad` | The athlete's full name                   | `Ayşe Yılmaz` |
| `Gün`      | Turkish weekday name                      | `Salı`        |
| `Saat`     | Start time, as a time cell or `HH:MM` text | `18:30`       |

Each row is one session, so an athlete who trains three times a week has three
rows. Other columns are ignored. Headers and weekday names are matched without
regard to case or Turkish accents, and `18.30` is read the same as `18:30`.
Rows with a missing name, an unknown day or an invalid time are skipped and
reported; the rest of the schedule still loads.

[`sample_schedule.xlsx`](sample_schedule.xlsx) is a working example with
fictional names.

> [!IMPORTANT]
> This is a public repository. Never commit a real schedule: `.gitignore`
> excludes every `.xlsx` file except the sample.

## Development

Requires [uv](https://docs.astral.sh/uv/). It installs the pinned Python 3.14
and all dependencies on first use.

```bash
uv sync                 # create the environment
uv run pytest           # tests
uv run ruff check       # lint
uv run ruff format      # format
uv run mypy             # type check
```

GitHub Actions runs the same checks on Windows and macOS for every pull request
and every push to `main`.

## License

[MIT](LICENSE)
