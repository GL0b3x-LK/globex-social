"""The client's review notes — one row per reviewer per post, upserted in place.

`post_id` None is a note on the whole batch. The unique key is
(batch, post_id, author) with NULLs treated as equal, so a reviewer editing
their note updates the same row rather than stacking a second opinion.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db.client import get_supabase, maybe_row, rows
from app.db.client import row as _row

Row = dict[str, Any]
_TABLE = "post_feedback"


def _existing(batch: str, post_id: str | None, author: str) -> Row | None:
    q = get_supabase().table(_TABLE).select("*").eq("batch", batch).eq("author", author)
    q = q.is_("post_id", "null") if post_id is None else q.eq("post_id", post_id)
    return maybe_row(q.limit(1).execute())


def upsert(
    *,
    batch: str,
    post_id: str | None,
    author: str,
    verdict: str | None,
    note: str | None,
) -> Row:
    payload: Row = {
        "batch": batch,
        "post_id": post_id,
        "author": author,
        "verdict": verdict or None,
        "note": (note or "").strip() or None,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    current = _existing(batch, post_id, author)
    if current:
        return _row(get_supabase().table(_TABLE).update(payload).eq("id", current["id"]).execute())
    return _row(get_supabase().table(_TABLE).insert(payload).execute())


def list_for_batch(batch: str) -> list[Row]:
    return rows(
        get_supabase().table(_TABLE).select("*").eq("batch", batch).order("updated_at").execute()
    )
