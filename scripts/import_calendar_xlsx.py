"""Import the client's calendar workbook into a calendar JSON the engine can walk.

    python scripts/import_calendar_xlsx.py "~/Downloads/Internal Version for Globex Only.xlsx" \\
        --out app/data/calendar_2026_27.json

Prints what was imported and any undated rows (the client's working notes),
so those get answered rather than lost.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import calendar_import  # noqa: E402


def main(src: Path, out: Path) -> int:
    entries, questions = calendar_import.read_workbook(src)
    calendar_import.write_calendar_json(entries, questions, out, source=src.name)
    print(f"{len(entries)} posts -> {out}")
    print("categories:", dict(Counter(e.category for e in entries)))
    print("templates :", dict(Counter(e.template for e in entries)))
    print(f"captions written by client: {sum(1 for e in entries if e.exact_caption)}")
    if questions:
        print("\nUndated rows (client notes, not posts):")
        for q in questions:
            print(f"  - {q}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--out", type=Path, default=Path("app/data/calendar_2026_27.json"))
    a = ap.parse_args()
    raise SystemExit(main(a.xlsx.expanduser(), a.out))
