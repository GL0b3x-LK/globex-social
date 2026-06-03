"""Seed reference data into Supabase from app/data/*.json. Idempotent.

Upserts on each table's natural key (name / slot_number), so running twice does
not duplicate rows. branded_packaging_rotation is skipped until its data lands
(the 20 posts are still outstanding — see docs/missing_assets.md).

Run:  .venv\\Scripts\\python.exe scripts\\seed_db.py
"""
from __future__ import annotations

import json
from pathlib import Path

from app.db import employees, holidays, trade_shows
from app.logging_config import configure_logging, get_logger

DATA = Path(__file__).resolve().parent.parent / "app" / "data"
log = get_logger("seed_db")


def _load(name: str) -> list[dict]:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def main() -> None:
    configure_logging()
    emp = _load("employees.json")
    hol = _load("holidays.json")
    shows = _load("trade_shows.json")

    employees.upsert_many(emp)
    holidays.upsert_many(hol)
    trade_shows.upsert_many(shows)

    log.info(
        "seed complete",
        extra={"employees": len(emp), "holidays": len(hol), "trade_shows": len(shows)},
    )


if __name__ == "__main__":
    main()
