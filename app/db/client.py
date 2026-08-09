"""Supabase client singleton + response unwrap helpers.

Server-side uses the service_role key (SUPABASE_KEY), which bypasses RLS. The
unwrap helpers narrow postgrest's loosely-typed ``response.data`` (``list[JSON]``)
to the row dicts our helpers actually return — one place to cast, not 28.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

from supabase import Client, ClientOptions, create_client

from app.config import get_settings

Row = dict[str, Any]


# Video masters are tens of megabytes; the storage client's default timeout is
# sized for images and cuts an upload off mid-flight.
_STORAGE_TIMEOUT_S = 600


@lru_cache
def get_supabase() -> Client:
    settings = get_settings()
    return create_client(
        settings.supabase_url,
        settings.supabase_key,
        options=ClientOptions(storage_client_timeout=_STORAGE_TIMEOUT_S),
    )


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
