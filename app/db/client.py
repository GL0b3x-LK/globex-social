"""Supabase client singleton + response unwrap helpers.

Server-side uses the service_role key (SUPABASE_KEY), which bypasses RLS. The
unwrap helpers narrow postgrest's loosely-typed ``response.data`` (``list[JSON]``)
to the row dicts our helpers actually return — one place to cast, not 28.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

from supabase import Client, create_client

from app.config import get_settings

Row = dict[str, Any]


@lru_cache
def get_supabase() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_key)


def rows(resp: Any) -> list[Row]:
    """All rows from a postgrest response."""
    return cast("list[Row]", resp.data)


def maybe_row(resp: Any) -> Row | None:
    """First row, or None when the result set is empty."""
    data = resp.data
    return cast("Row", data[0]) if data else None


def row(resp: Any) -> Row:
    """First row from a write that returns its representation (insert/update/upsert)."""
    return cast("Row", resp.data[0])


def ping() -> bool:
    """Lightweight connectivity check for /health. Raises on failure."""
    get_supabase().table("employees").select("id").limit(1).execute()
    return True
