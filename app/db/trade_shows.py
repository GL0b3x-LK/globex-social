"""Trade show query helpers."""

from __future__ import annotations

from typing import Any

from app.db.client import get_supabase, maybe_row, rows

Row = dict[str, Any]
_TABLE = "trade_shows"


def upsert_many(records: list[Row]) -> list[Row]:
    return rows(get_supabase().table(_TABLE).upsert(records, on_conflict="name").execute())


def list_all() -> list[Row]:
    return rows(get_supabase().table(_TABLE).select("*").order("name").execute())


def list_visible() -> list[Row]:
    """Shows the scheduler may act on (not hidden)."""
    return rows(get_supabase().table(_TABLE).select("*").eq("hidden", False).execute())


def needs_confirmation() -> list[Row]:
    """Visible shows still awaiting a confirmed date (weekly digest source)."""
    return rows(
        get_supabase()
        .table(_TABLE)
        .select("*")
        .eq("hidden", False)
        .eq("needs_date_confirmation", True)
        .execute()
    )


def get_by_name(name: str) -> Row | None:
    return maybe_row(get_supabase().table(_TABLE).select("*").eq("name", name).limit(1).execute())
