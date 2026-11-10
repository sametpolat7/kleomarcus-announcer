"""Generate ``sample_schedule.xlsx``, a schedule of fictional athletes.

Run with ``uv run scripts/make_sample_schedule.py``.
"""

from datetime import time
from pathlib import Path

from openpyxl import Workbook

from announcer.loader import REQUIRED_COLUMNS

OUTPUT = Path(__file__).resolve().parent.parent / "sample_schedule.xlsx"

# Every name here is made up. Never add real athletes to this file.
# The rows are in alphabetical order of the names.
ROWS: tuple[tuple[str, str, time], ...] = (
    ("Ayşe Yılmaz", "Pazartesi", time(18, 0)),
    ("Ayşe Yılmaz", "Çarşamba", time(18, 0)),
    ("Burak Aydın", "Cumartesi", time(11, 0)),
    ("Can Öztürk", "Salı", time(17, 30)),
    ("Can Öztürk", "Perşembe", time(17, 30)),
    ("Deniz Arslan", "Perşembe", time(17, 30)),
    ("Elif Şahin", "Salı", time(19, 0)),
    ("Elif Şahin", "Pazar", time(12, 30)),
    ("Emre Çelik", "Çarşamba", time(20, 0)),
    ("Mehmet Demir", "Pazartesi", time(18, 0)),
    ("Zeynep Kaya", "Pazartesi", time(19, 30)),
    ("Zeynep Kaya", "Cuma", time(19, 30)),
)


def main() -> None:
    workbook = Workbook()
    sheet = workbook.worksheets[0]
    sheet.title = "Program"
    sheet.append(REQUIRED_COLUMNS)
    for row in ROWS:
        sheet.append(row)
        sheet.cell(row=sheet.max_row, column=3).number_format = "hh:mm"
    sheet.column_dimensions["A"].width = 24
    sheet.column_dimensions["B"].width = 14
    workbook.save(OUTPUT)
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
