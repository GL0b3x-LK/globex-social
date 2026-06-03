"""Holiday query helpers."""
from __future__ import annotations

from typing import Any

from app.db.client import get_supabase, maybe_row, rows

Row = dict[str, Any]
_TABLE = "holidays"


def upsert_many(records: list[Row]) -> list[Row]:
    return rows(get_supabase().table(_TABLE).upsert(records, on_conflict="name").execute())


def list_all() -> list[Row]:
    return rows(get_supabase().table(_TABLE).select("*").order("name").execute())


def get_by_name(name: str) -> Row | None:
    return maybe_row(
        get_supabase().table(_TABLE).select("*").eq("name", name).limit(1).execute()
    )


def by_month(month: str) -> list[Row]:
    return rows(get_supabase().table(_TABLE).select("*").eq("month", month).execute())


def month_long() -> list[Row]:
    return rows(get_supabase().table(_TABLE).select("*").eq("is_month_long", True).execute())
