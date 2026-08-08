"""Branded-packaging rotation helpers (finite pool of 20 slots)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db.client import get_supabase, row, rows

Row = dict[str, Any]
_TABLE = "branded_packaging_rotation"


def upsert_many(records: list[Row]) -> list[Row]:
    return rows(get_supabase().table(_TABLE).upsert(records, on_conflict="slot_number").execute())


def list_active() -> list[Row]:
    return rows(get_supabase().table(_TABLE).select("*").eq("active", True).execute())


def next_slot() -> Row | None:
    """Fairest next slot: active, least-recently-posted (NULL = never posted first).

    Sorted in Python over the small (<=20) active set to avoid NULLS-FIRST
    ordering quirks across postgrest versions.
    """
    active = list_active()
    if not active:
        return None
    return min(
        active,
        key=lambda r: (r.get("last_posted_at") is not None, r.get("last_posted_at") or ""),
    )


def mark_posted(slot_number: int, when: str | None = None) -> Row:
    ts = when or datetime.now(UTC).isoformat()
    return row(
        get_supabase()
        .table(_TABLE)
        .update({"last_posted_at": ts})
        .eq("slot_number", slot_number)
        .execute()
    )
