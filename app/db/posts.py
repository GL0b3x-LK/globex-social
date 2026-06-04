"""Post lifecycle query helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db.client import get_supabase, maybe_row, row, rows

Row = dict[str, Any]
_TABLE = "posts"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create(
    *,
    content: str | None = None,
    caption: str | None = None,
    hashtags: list[str] | None = None,
    template_type: str | None = None,
    event_id: str | None = None,
    event_type: str | None = None,
    status: str = "draft",
) -> Row:
    payload: Row = {
        "content": content,
        "caption": caption,
        "hashtags": hashtags or [],
        "template_type": template_type,
        "event_id": event_id,
        "event_type": event_type,
        "status": status,
    }
    return row(get_supabase().table(_TABLE).insert(payload).execute())


def get(post_id: str) -> Row | None:
    return maybe_row(get_supabase().table(_TABLE).select("*").eq("id", post_id).limit(1).execute())


def update(post_id: str, **fields: Any) -> Row:
    return row(get_supabase().table(_TABLE).update(fields).eq("id", post_id).execute())


def set_status(post_id: str, status: str) -> Row:
    """Update status, stamping approved_at / published_at where relevant."""
    fields: Row = {"status": status}
    if status == "approved":
        fields["approved_at"] = _now()
    elif status == "published":
        fields["published_at"] = _now()
    return update(post_id, **fields)


def set_image_url(post_id: str, image_url: str) -> Row:
    return update(post_id, image_url=image_url)


def list_by_status(status: str) -> list[Row]:
    return rows(get_supabase().table(_TABLE).select("*").eq("status", status).execute())


def recent(limit: int = 30) -> list[Row]:
    """Newest posts first — feeds the conversational Q&A 'posts digest'."""
    return rows(
        get_supabase()
        .table(_TABLE)
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )


def set_render_meta(post_id: str, render_meta: dict[str, Any]) -> Row:
    """Persist the full render inputs so the post can be re-opened/edited later."""
    return update(post_id, render_meta=render_meta)


def set_target_platforms(post_id: str, platforms: list[str]) -> Row:
    """Set which platforms a post publishes to (e.g. ['linkedin'])."""
    return update(post_id, target_platforms=platforms)


def find_for_event(event_id: str, event_type: str) -> Row | None:
    """Scheduler idempotency: has a post already been made for this event?"""
    return maybe_row(
        get_supabase()
        .table(_TABLE)
        .select("*")
        .eq("event_id", event_id)
        .eq("event_type", event_type)
        .limit(1)
        .execute()
    )


def delete(post_id: str) -> None:
    get_supabase().table(_TABLE).delete().eq("id", post_id).execute()
